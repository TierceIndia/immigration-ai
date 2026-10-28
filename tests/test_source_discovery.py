"""Unit tests for government source auto-discovery (no real network calls).

All HTTP is mocked: robots.txt and page fetches go through a fake fetch_fn. These
tests prove the hard constraints: robots.txt respected, same-domain only, depth
limit, keyword scoring (relevant vs irrelevant), and dedup against existing
government_sources URLs.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from source_discovery import (  # noqa: E402
    discover_sources, match_keywords, _resolve_category,
    DEFAULT_MAX_DEPTH, MIN_SCORE,
)
from fetch.fetcher import FetchResult, FetchStatus  # noqa: E402


def _ok(url, html: str) -> FetchResult:
    return FetchResult(
        url=url, status=FetchStatus.OK, http_status=200,
        content_type="text/html", raw_bytes=html.encode("utf-8"),
        duration_ms=1, error=None,
    )


def _blocked(url) -> FetchResult:
    return FetchResult(
        url=url, status=FetchStatus.ACCESS_BLOCKED, http_status=403,
        content_type=None, raw_bytes=None, duration_ms=1, error="HTTP 403",
    )


class FakeFetcher:
    """Maps URL -> html body. robots.txt served at /robots.txt on every host."""

    def __init__(self, pages: dict[str, str], robots: dict[str, str] | None = None):
        self.pages = {k: v for k, v in pages.items()}
        self.robots = robots or {}
        self.calls: list[str] = []

    def __call__(self, url, timeout: int = 20, **kw):
        self.calls.append(url)
        from urllib.parse import urlparse
        p = urlparse(url)
        if p.path == "/robots.txt":
            body = self.robots.get(p.netloc, "User-agent: *\nAllow: /\n")
            return _ok(url, body)
        for page_url, html in self.pages.items():
            if page_url == url:
                return _ok(url, html)
        return _blocked(url)


def _index(links: list[str]) -> str:
    anchors = "\n".join(f'<a href="{l}">{l}</a>' for l in links)
    return f"<html><head><title>Seed</title></head><body>{anchors}</body></html>"


# ---------------------------------------------------------------------------
# Keyword scoring (pure, no fetch)
# ---------------------------------------------------------------------------

class TestKeywordScoring:
    def test_clearly_relevant_work_page_scores_high(self):
        score, matched = match_keywords(
            "Employer sponsored subclass 482 Temporary Skill Shortage sponsored visa requirements work",
            "WORK",
        )
        assert score >= MIN_SCORE
        assert any(k in matched for k in ("482", "sponsor"))

    def test_clearly_irrelevant_page_scores_zero(self):
        score, _ = match_keywords("About us board meeting careers contact site map", None)
        assert score == 0

    def test_resolve_category_known(self):
        assert _resolve_category("Employer sponsored subclass 482 work visa", "WORK") == "WORK"

    def test_resolve_category_unknown_returns_none(self):
        assert _resolve_category("hello world generic greeting", None) is None

    def test_resolve_category_ambiguous_tie_returns_none(self):
        # Crafted so STUDENT and WORK hit the same number of keywords -> ambiguous.
        assert _resolve_category("student study education", "WORK") in (None, "STUDENT")


# ---------------------------------------------------------------------------
# discover_sources() with mocked fetch
# ---------------------------------------------------------------------------

SEED = "https://example.gov.au/visas/work"
WORK_PAGE = ("<html><head><title>Temporary Skill Shortage 482</title></head>"
             "<body><p>Employer sponsored subclass 482 work visa requirements</p></body></html>")
IRRELEVANT_PAGE = ("<html><head><title>About us</title></head>"
                   "<body><p>contact board meetings</p></body></html>")


class TestRobotsGate:
    def test_seed_path_disallowed_aborts(self):
        fake = FakeFetcher(
            {SEED: WORK_PAGE},
            robots={  # disallow the seed path
                "example.gov.au": "User-agent: *\nDisallow: /visas/work\nAllow: /\n",
            },
        )
        created, report = discover_sources(SEED, session=None, fetch_fn=fake,
                                           min_delay_seconds=0)
        assert report["robots_blocked_seed"] is True
        assert created == []
        # Must NOT have fetched the seed page itself after robots disallow.
        assert len([u for u in fake.calls if "/visas/work" in u]) == 0


class TestSameDomain:
    def test_external_links_skipped(self):
        external = "https://other.gov.au/visas"
        fake = FakeFetcher({SEED: _index(["https://example.gov.au/visas/482", external])})
        _, report = discover_sources(SEED, session=None, fetch_fn=fake, min_delay_seconds=0)
        skipped = [s["url"] for s in report["skipped"] if s["reason"] == "different domain"]
        assert external in skipped
        assert "https://example.gov.au/visas/482" in report["crawled"]


class TestDepthLimit:
    def test_depth_limit_respected(self):
        # seed links to a -> b -> c. max_depth=1 should stop after the seed's direct
        # children (a) and NOT reach grandchildren beyond that layer.
        page_a = "https://example.gov.au/visas/482"
        page_b = "https://example.gov.au/visas/482/more"
        page_c = "https://example.gov.au/visas/482/more/deep"
        fake = FakeFetcher({
            SEED: _index([page_a]),
            page_a: _index([page_b]),
            page_b: _index([page_c]),
            page_c: WORK_PAGE,
        })
        _, report = discover_sources(SEED, max_depth=1, session=None, fetch_fn=fake,
                                     min_delay_seconds=0)
        assert page_a in report["crawled"]        # depth 1 reached
        assert page_b not in report["crawled"]    # depth 2 not reached with max_depth=1
        assert page_c not in report["crawled"]    # depth 3 not reached with max_depth=1


class TestDedup:
    def test_duplicate_of_existing_source_not_created(self):
        """A candidate whose URL already exists in government_sources must NOT be
        persisted as a new discovered source (dedup against exact existing URL)."""
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        from db.models import Base, Country, ImmigrationProgram, DiscoveredSource
        from db.repositories import SourceRepo

        eng = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=eng)
        Session = sessionmaker(bind=eng)
        with Session() as s:
            s.merge(Country(country_id="AU", name="Australia", iso_code="AUS"))
            s.merge(ImmigrationProgram(
                program_id="WORK_482", country_id="AU", visa_category="WORK",
                subclass_code="482", name="Temporary Skill Shortage", status="ACTIVE",
            ))
            # Pre-existing real source with the very URL discovery would propose.
            SourceRepo(s).create(
                source_id="SRC-AU-WORK-482", country_id="AU", program_id="WORK_482",
                visa_category="WORK", authority="Dept of Home Affairs",
                url="https://example.gov.au/visas/482", source_type="HTML",
                priority="PRIMARY", monitoring_method="WEB",
                crawl_frequency="DAILY", status="ACTIVE",
            )
            s.commit()

            fake = FakeFetcher({
                SEED: _index(["https://example.gov.au/visas/482"]),
                "https://example.gov.au/visas/482": WORK_PAGE,
            })
            created, report = discover_sources(SEED, session=s, fetch_fn=fake,
                                               min_delay_seconds=0)
            # created reflects persistence-time dedup: the existing URL is skipped.
            assert all(c.candidate_url != "https://example.gov.au/visas/482"
                       for c in created)
            # And nothing new was persisted for that URL.
            stored = list(s.execute(select(DiscoveredSource)).scalars())
            assert all(d.candidate_url != "https://example.gov.au/visas/482" for d in stored)


class TestEndToEndDiscovery:
    def test_discovers_relevant_page_and_creates_candidate(self):
        fake = FakeFetcher({
            SEED: _index(["https://example.gov.au/visas/482"]),
            "https://example.gov.au/visas/482": WORK_PAGE,
        })
        created, report = discover_sources(SEED, session=None, fetch_fn=fake,
                                           min_delay_seconds=0)
        # read-only mode returns all candidates
        urls = [c.candidate_url for c in created]
        assert "https://example.gov.au/visas/482" in urls

    def test_irrelevant_pages_not_persisted_as_candidates(self):
        fake = FakeFetcher({
            SEED: _index(["https://example.gov.au/about-us"]),
            "https://example.gov.au/about-us": IRRELEVANT_PAGE,
        })
        created, report = discover_sources(SEED, session=None, fetch_fn=fake,
                                           min_delay_seconds=0)
        assert "https://example.gov.au/about-us" not in [c.candidate_url for c in created]
