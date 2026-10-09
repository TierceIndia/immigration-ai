"""Optional static egress proxy: configuration, redaction and fail-closed wiring.

The proxy is a *single fixed* value for reachability/egress control, not a
rotation mechanism. These tests pin three properties:

* default off — no env var means every call goes direct (``proxies=None``);
* fail-closed — a malformed ``FETCH_PROXY_URL`` produces a classified failure
  and **no** outbound request, on both the plain and browser paths;
* no credential leak — ``user:password@`` userinfo never reaches an error
  string, a log line or a ``FetchResult``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import fetch.fetcher as fetcher
from fetch import proxy
from fetch.browser_fetcher import _patchright_fetch
from fetch.fetcher import FetchStatus
from fetch.proxy import (
    ENV_VAR,
    ProxyConfigError,
    browser_proxy,
    configured_proxy_url,
    parse_proxy_url,
    redact,
    requests_proxies,
)


VALID = "http://proxy.internal:3128"
VALID_WITH_CREDS = "http://alice:s3cr3t@proxy.internal:3128"


class _FakeResponse:
    def __init__(self, status_code=200, text="ok", content=b"<html>" + b"x" * 80):
        self.status_code = status_code
        self.text = text
        self.content = content
        self.headers = {"Content-Type": "text/html"}


def _no_network(monkeypatch):
    """Make any real outbound call fail the test rather than reach the network."""

    def boom(*args, **kwargs):
        raise AssertionError(f"the network was touched: {args!r}")

    monkeypatch.setattr(fetcher.requests, "get", boom)


# ===========================================================================
# Configuration
# ===========================================================================
class TestConfiguration:
    def test_unset_means_direct(self, monkeypatch):
        monkeypatch.delenv(ENV_VAR, raising=False)
        assert configured_proxy_url() is None
        assert requests_proxies() is None
        assert browser_proxy() is None

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_blank_is_the_same_as_unset(self, monkeypatch, blank):
        monkeypatch.setenv(ENV_VAR, blank)
        assert configured_proxy_url() is None
        assert requests_proxies() is None
        assert browser_proxy() is None

    def test_surrounding_whitespace_is_trimmed(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, f"  {VALID}  ")
        assert configured_proxy_url() == VALID
        assert requests_proxies() == {"http": VALID, "https": VALID}

    def test_a_valid_proxy_is_used_for_both_schemes(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, VALID)
        assert requests_proxies() == {"http": VALID, "https": VALID}

    def test_the_browser_sees_the_same_server(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, VALID)
        assert browser_proxy() == {"server": VALID}

    def test_credentials_reach_the_transport_but_in_the_right_shape(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, VALID_WITH_CREDS)
        assert requests_proxies()["https"] == VALID_WITH_CREDS
        assert browser_proxy() == {
            "server": "http://proxy.internal:3128",
            "username": "alice",
            "password": "s3cr3t",
        }

    @pytest.mark.parametrize("scheme", ["http", "https"])
    def test_accepted_schemes(self, monkeypatch, scheme):
        url = f"{scheme}://proxy.internal:1080"
        monkeypatch.setenv(ENV_VAR, url)
        assert requests_proxies() == {"http": url, "https": url}


# ===========================================================================
# Fail closed
# ===========================================================================
class TestMalformedConfiguration:
    @pytest.mark.parametrize(
        "bad",
        [
            "proxy.internal:3128",       # no scheme
            "ftp://proxy.internal:21",   # unsupported scheme
            # SOCKS is deliberately unsupported: requests needs PySocks
            # (requests[socks]) for a real socks5 proxy, which this project
            # does not declare or install. Accepting the scheme here would
            # pass validation and then fail at request time with a confusing
            # dependency error instead of this module's clean, immediate
            # ProxyConfigError - see proxy.py's _ALLOWED_SCHEMES comment.
            "socks5://proxy.internal:1080",
            "socks5h://proxy.internal:1080",
            "http://",                   # no host
            "http://proxy.internal:99999",  # port out of range
            "://nope",
        ],
    )
    def test_it_is_rejected_not_ignored(self, monkeypatch, bad):
        monkeypatch.setenv(ENV_VAR, bad)
        with pytest.raises(ProxyConfigError):
            parse_proxy_url(bad)
        with pytest.raises(ProxyConfigError):
            requests_proxies()
        with pytest.raises(ProxyConfigError):
            browser_proxy()

    def test_plain_fetch_refuses_and_never_calls_the_network(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "proxy.internal:3128")
        _no_network(monkeypatch)

        res = fetcher.fetch("https://immi.homeaffairs.gov.au/189")

        assert res.status == FetchStatus.NETWORK_RESTRICTED
        assert res.error_type == "proxy_config_error"
        assert res.raw_bytes is None

    def test_browser_fetch_refuses_before_launching_anything(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "ftp://proxy.internal:21")
        # The proxy check runs before the patchright import, so this returns a
        # classified failure even where no browser is installed.
        res = _patchright_fetch("https://immi.homeaffairs.gov.au/189", "HTML", 5)

        assert res.status == FetchStatus.NETWORK_RESTRICTED
        assert res.error_type == "proxy_config_error"

    def test_a_misconfiguration_never_escalates_to_the_browser(self, monkeypatch):
        # NETWORK_RESTRICTED is not an escalation trigger, so a broken proxy
        # cannot quietly become a direct browser fetch.
        from utils import fetch_document

        assert (
            FetchStatus.NETWORK_RESTRICTED
            not in fetch_document._BROWSER_ESCALATION_STATUSES
        )


# ===========================================================================
# Wiring: the proxy actually reaches the transport
# ===========================================================================
class TestWiring:
    def test_requests_get_receives_the_configured_proxy(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, VALID)
        seen: list[tuple[str, dict]] = []

        def fake_get(url, **kwargs):
            seen.append((url, kwargs))
            return _FakeResponse()

        monkeypatch.setattr(fetcher.requests, "get", fake_get)

        res = fetcher.fetch("https://immi.homeaffairs.gov.au/189")

        assert res.status == FetchStatus.OK
        target = [k for u, k in seen if u == "https://immi.homeaffairs.gov.au/189"]
        assert target and target[0]["proxies"] == {"http": VALID, "https": VALID}

    def test_robots_is_fetched_through_the_same_proxy(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, VALID)
        seen: list[tuple[str, dict]] = []

        def fake_get(url, **kwargs):
            seen.append((url, kwargs))
            return _FakeResponse(text="")  # empty robots => no crawl-delay

        monkeypatch.setattr(fetcher.requests, "get", fake_get)

        fetcher.fetch("https://immi.homeaffairs.gov.au/189")

        robots = [k for u, k in seen if u.endswith("/robots.txt")]
        assert robots and robots[0]["proxies"] == {"http": VALID, "https": VALID}

    def test_direct_when_no_proxy_is_configured(self, monkeypatch):
        monkeypatch.delenv(ENV_VAR, raising=False)
        seen: list[tuple[str, dict]] = []

        def fake_get(url, **kwargs):
            seen.append((url, kwargs))
            return _FakeResponse()

        monkeypatch.setattr(fetcher.requests, "get", fake_get)

        fetcher.fetch("https://immi.homeaffairs.gov.au/189")

        for _url, kwargs in seen:
            assert kwargs.get("proxies") is None


# ===========================================================================
# Static, never rotated
# ===========================================================================
class TestStaticNeverRotated:
    def test_the_same_value_is_used_across_repeated_calls(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, VALID)
        first = requests_proxies()
        second = requests_proxies()
        assert first == second == {"http": VALID, "https": VALID}

    def test_a_refusal_does_not_change_the_egress(self, monkeypatch):
        # Two fetches, the second answering 403: the proxy for each must be the
        # same value. Nothing here reacts to a response by changing identity.
        monkeypatch.setenv(ENV_VAR, VALID)
        seen: list[dict] = []

        def fake_get(url, **kwargs):
            seen.append(kwargs)
            if url.endswith("/robots.txt"):
                return _FakeResponse(text="")
            return _FakeResponse(status_code=403, text="Forbidden")

        monkeypatch.setattr(fetcher.requests, "get", fake_get)

        fetcher.fetch("https://refusing.example.invalid/189", max_retries=1)
        fetcher.fetch("https://refusing.example.invalid/189", max_retries=1)

        used = [k.get("proxies") for k in seen]
        assert used, "the fetch should have happened"
        assert all(p == {"http": VALID, "https": VALID} for p in used)

    def test_the_module_offers_no_rotation_api(self):
        assert not any(
            "rotate" in name.lower() or "pool" in name.lower()
            for name in dir(proxy)
        )


# ===========================================================================
# Credential redaction
# ===========================================================================
class TestRedaction:
    def test_password_is_hidden(self):
        shown = redact(VALID_WITH_CREDS)
        assert "s3cr3t" not in shown
        assert "alice" not in shown
        assert "***@proxy.internal:3128" in shown

    def test_a_credential_free_url_is_unchanged(self):
        assert redact(VALID) == VALID

    def test_scheme_host_and_port_survive_so_it_stays_diagnosable(self):
        shown = redact("socks5://user:pw@10.0.0.9:1080")
        assert shown == "socks5://***@10.0.0.9:1080"

    @pytest.mark.parametrize(
        "bad",
        ["http://[unclosed", "http://user:pw@:notaport"],
    )
    def test_unparseable_values_do_not_echo_the_secret(self, bad):
        shown = redact(bad)
        assert "pw" not in shown

    def test_fetch_error_never_contains_the_password(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "http://alice:s3cr3t@proxy.internal:notaport")
        _no_network(monkeypatch)

        res = fetcher.fetch("https://immi.homeaffairs.gov.au/189")

        assert res.error is not None
        assert "s3cr3t" not in res.error
        assert "alice" not in res.error
