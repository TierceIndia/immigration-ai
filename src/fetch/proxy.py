"""Optional single, static egress proxy for outbound retrieval.

The operator may point every outbound request at **one fixed** proxy via the
``FETCH_PROXY_URL`` env var (default: unset, i.e. direct). This exists for
network *reachability and egress control* — a host that only permits one known
exit IP, a corporate egress gateway, a sandbox that must route through a
recorded chokepoint. It is deliberately **not** a rotation mechanism:

* there is exactly one proxy URL in the entire process, read once per call;
* it is never changed in response to a refusal, a 403/429, or a cooldown;
* it is the same for every request for the whole lifetime of the process.

That is why this does not contradict ``fetcher.py``'s no-evasion policy. That
policy forbids *rotation* (cycling identities to dodge a block); a static,
operator-declared exit is just where our traffic leaves the network. See
``fetcher.py``'s module docstring for the policy itself.

Two properties matter and both are enforced here:

* **Fail-closed.** If ``FETCH_PROXY_URL`` is set but is not a usable proxy URL
  (unparseable, missing host, unsupported scheme), the fetch path must *refuse
  to go direct*. Silently ignoring a broken proxy and connecting straight to a
  government host is exactly the behaviour that gets us blocked, so a
  misconfiguration is surfaced as a classified fetch failure instead.
* **Credential redaction.** A proxy URL may embed ``user:password@``. That
  userinfo must never appear in a log line, an error string or a ``FetchResult``
  error field, so every external rendering goes through :func:`redact`.
"""
from __future__ import annotations

import os
from urllib.parse import SplitResult, urlsplit

ENV_VAR = "FETCH_PROXY_URL"

# Schemes we actually support end-to-end. SOCKS is deliberately excluded even
# though `requests` can parse a socks5:// proxies value: real SOCKS support
# needs PySocks installed (`requests[socks]`), which this project does not
# declare or install - accepting the scheme here would pass validation and
# then fail at request time with a dependency error instead of the clean,
# immediate ProxyConfigError this module exists to guarantee. The spec this
# module implements only ever asks for http://user:pass@host:port; add socks
# support (scheme + dependency) together if it is ever actually needed.
_ALLOWED_SCHEMES = ("http", "https")


class ProxyConfigError(ValueError):
    """``FETCH_PROXY_URL`` is set but unusable — fail closed, do not fetch."""


def configured_proxy_url() -> str | None:
    """The raw proxy URL from the environment, or ``None`` when disabled."""
    raw = os.getenv(ENV_VAR)
    if raw is None or not raw.strip():
        return None
    return raw.strip()


def parse_proxy_url(url: str) -> "SplitResult":
    """Validate ``url`` and return its parsed parts, or raise ``ProxyConfigError``.

    Rejects anything that is not a complete ``scheme://host[:port]`` with a
    scheme ``requests`` understands, so a malformed value can never be passed
    through to the HTTP layer where it might be ignored and silently fall back
    to a direct connection.
    """
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise ProxyConfigError(f"{ENV_VAR} is not a parseable URL: {exc}") from exc

    if not parts.scheme:
        raise ProxyConfigError(f"{ENV_VAR} is missing a scheme (e.g. http://host:port)")
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ProxyConfigError(
            f"{ENV_VAR} uses unsupported scheme {parts.scheme!r}; "
            f"expected one of {', '.join(_ALLOWED_SCHEMES)}"
        )
    if not parts.hostname:
        raise ProxyConfigError(f"{ENV_VAR} is missing a host")
    try:
        parts.port  # noqa: B018 - accessing .port raises on a malformed port
    except ValueError as exc:
        raise ProxyConfigError(f"{ENV_VAR} has an invalid port: {exc}") from exc
    return parts


def redact(url: str) -> str:
    """Return ``url`` safe to put in a log line, error or test assertion.

    Any ``user:password@`` userinfo is replaced with ``***``; everything else
    (scheme, host, port, path) is preserved so the value stays diagnosable.
    A value we cannot parse is reported as a placeholder rather than echoed, in
    case the secret is inside the part that failed to parse.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable proxy url>"
    if not parts.netloc:
        return url if "@" not in url else "<unparseable proxy url>"
    host = parts.hostname or ""
    if not host:
        return "<unparseable proxy url>"
    netloc = host
    try:
        port = parts.port
    except ValueError:
        return "<unparseable proxy url>"
    if port:
        netloc = f"{host}:{port}"
    if parts.username or parts.password:
        netloc = f"***@{netloc}"
    return f"{parts.scheme}://{netloc}{parts.path}"


def requests_proxies() -> dict[str, str] | None:
    """Return a ``requests`` proxies mapping, ``None`` when no proxy is set.

    Raises ``ProxyConfigError`` when ``FETCH_PROXY_URL`` is set but malformed:
    callers must treat that as a hard failure, never as "no proxy".
    """
    url = configured_proxy_url()
    if url is None:
        return None
    parse_proxy_url(url)
    return {"http": url, "https": url}


def browser_proxy() -> dict[str, str] | None:
    """Return a Playwright ``proxy=`` mapping, ``None`` when no proxy is set.

    Raises ``ProxyConfigError`` on malformed configuration, matching
    ``requests_proxies()`` so both retrieval paths fail closed together.
    """
    url = configured_proxy_url()
    if url is None:
        return None
    parts = parse_proxy_url(url)
    server = f"{parts.scheme}://{parts.hostname}"
    if parts.port:
        server = f"{server}:{parts.port}"
    proxy: dict[str, str] = {"server": server}
    if parts.username:
        proxy["username"] = parts.username
    if parts.password:
        proxy["password"] = parts.password
    return proxy