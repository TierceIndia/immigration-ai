"""Government source auto-discovery — an onboarding/suggestion tool.

Given ONE seed URL (e.g. a section homepage on immi.homeaffairs.gov.au), discover
which sub-pages under that URL look like genuine immigration-eligibility content
worth monitoring, and propose them as *candidates* for government_sources.

It is explicitly NOT a replacement for any part of the existing pipeline:
  - It sits upstream of government_sources and proposes rows for that table.
  - It never inserts into government_sources directly, and never triggers
    extraction, rule compilation, or activation on its own. Only an admin approve
    action promotes a candidate into a real source.

Hard constraints enforced here:
  - robots.txt respected (abort entirely if the seed's own path is disallowed).
  - Same-domain only: never follow a link to a different host than the seed's.
  - Depth-limited crawl (default max_depth=2).
  - Polite rate limiting between same-domain requests.
  - No new fetch path: reuses fetcher.fetch().
  - No direct provider LLM calls: any classification LLM second-opinion goes through
    the governance layer (src/llm_governance.py) + extraction.llm_client, and is skipped
    gracefully when no real LLM is configured.
  - Deduplicates against existing government_sources.url values (exact match).
  - Never invents classifications: an ambiguous page leaves
    suggested_visa_category=None for the admin to assign.
"""
from __future__ import annotations

import os
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import dotenv
from bs4 import BeautifulSoup

from db.repositories import DiscoveredSourceRepo, SourceRepo
from fetch.fetcher import FetchResult, fetch as _default_fetch

# In TESTS, dotenv loading is suppressed so a developer's .env cannot leak
# keys into the hermetic suite (conftest strips them at module load).
if not os.environ.get("TESTING"):
    dotenv.load_dotenv()

# ---------------------------------------------------------------------------
# Category-aware keyword lists (easy to find / edit; bitmap of which sub-pages are
# relevance for each visa category). Keys are the canonical visa_category strings.
# ---------------------------------------------------------------------------
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "SKILLED": [
        "skilled", "independent", "nominated", "regional", "points", "skillselect",
        "189", "190", "491", "occupation", "skills assessment",
    ],
    "WORK": [
        "work", "employer", "sponsor", "sponsored", "skills in demand",
        "labour agreement", "482", "494", "temporary skill shortage",
        "tss", "subclass 482",
    ],
    "STUDENT": [
        "student", "cricos", "confirmation of enrolment", "coe", "500",
        "study", "education", "guardian", "elicos",
    ],
    "VISITOR": [
        "visitor", "tourist", "600", "eta", "evisitor", "holiday", "leisure",
        "valid passport", "electronic travel authority",
    ],
}

# A page not matching any category is "unknown" — suggested_visa_category stays None.
MIN_SCORE = 20        # below this we do not even persist a candidate
BORDERLINE_LOW = 34   # LLM second-opinion band (middle third of 0-100)
BORDERLINE_HIGH = 66

DEFAULT_MAX_DEPTH = 2
DEFAULT_MIN_DELAY_SECONDS = 1.0
DEFAULT_TIMEOUT_SECONDS = 20

# Seed ourselves a default category for scoring. If the caller passes
# suggested_category, we bias the keyword match toward it (see score_page).


@dataclass
class DiscoveredCandidate:
    """A candidate page proposed by discovery (pre-persistence view)."""
    candidate_url: str
    page_title: str | None = None
    matched_keywords: list[str] = field(default_factory=list)
    confidence_score: int = 0
    suggested_visa_category: str | None = None

    def to_create_kwargs(self, seed_url: str, discovery_run_id: str | None) -> dict:
        return {
            "discovered_source_id": _candidate_id(self.candidate_url),
            "seed_url": seed_url,
            "candidate_url": self.candidate_url,
            "page_title": self.page_title,
            "matched_keywords": self.matched_keywords,
            "confidence_score": self.confidence_score,
            "suggested_visa_category": self.suggested_visa_category,
            "suggested_country_id": "AU",
            "discovery_run_id": discovery_run_id,
        }


def _candidate_id(url: str) -> str:
    """Deterministic short ID derived from the URL, consistent with the short-string
    ID convention used across the source-registry domain (e.g. SRC-...)."""
    u = urlparse(url)
    path_slugs = [s for s in u.path.split("/") if s]
    key = "-".join(path_slugs[-2:]) if path_slugs else (u.hostname or "disc")
    key = "".join(c if c.isalnum() else "-" for c in key)
    key = key.strip("-").upper()
    return f"DISC-{u.hostname or 'host'}-{key}"[:32]


def _host_of(url: str) -> str | None:
    return urlparse(url).hostname


def _is_same_domain(url: str, base_host: str) -> bool:
    host = _host_of(url)
    if not host:
        return False
    return host == base_host or host.endswith("." + base_host)


class RobotsGate:
    """Minimal robots.txt gate using the stdlib parser. Fetch is done via our own
    fetcher.fetch so we obey the same timeout/polite conventions."""

    def __init__(self, base_url: str, fetch_fn=_default_fetch):
        self.base_url = base_url
        self._parser: urllib.robotparser.RobotFileParser | None = None
        self._fetch_fn = fetch_fn
        self._load()

    def _robots_url(self) -> str:
        parts = urlparse(self.base_url)
        return f"{parts.scheme}://{parts.netloc}/robots.txt"

    def _load(self) -> None:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(self._robots_url())
        # Provide raw fetch through our polite fetcher rather than urllib's default.
        res: FetchResult = self._fetch_fn(self._robots_url(), timeout=DEFAULT_TIMEOUT_SECONDS)
        if not res.ok:
            # If we cannot verify robots.txt (blocked/error), default to no crawling to be
            # safe: the parser will be treated as forbidding everything.
            self._parser = rp
            self._parser.allow_all = False
            return
        body = (res.raw_bytes or b"").decode("utf-8", errors="replace")
        rp.parse(body.splitlines())
        self._parser = rp

    def can_fetch(self, url: str) -> bool:
        if self._parser is None:
            return False
        return self._parser.can_fetch(USER_AGENT_TOKEN, url)


USER_AGENT_TOKEN = "*"


def match_keywords(page_text: str, category: str | None = None) -> tuple[int, list[str]]:
    """Score a page (title + url + text) against the category-aware keyword lists.

    Returns (score 0-100, matched_keywords). If `category` is given we weight that
    category's keywords more heavily (bias for seed-time intent) but still allow the
    strongest other-category match to win when a page is clearly for another stream.
    """
    text_lower = (page_text or "").lower()
    if not text_lower:
        return 0, []

    matches: list[str] = []
    per_category_hits: dict[str, int] = {}

    # For each category, count how many of its keywords appear as substrings.
    for cat, kws in CATEGORY_KEYWORDS.items():
        cat_hits = 0
        for kw in kws:
            if kw in text_lower:
                cat_hits += 1
                if kw not in matches:
                    matches.append(kw)
        per_category_hits[cat] = cat_hits

    best_cat = max(per_category_hits, key=lambda c: per_category_hits[c])
    best_hits = per_category_hits[best_cat]
    num_keywords = len(CATEGORY_KEYWORDS.get(best_cat, []))

    if best_hits == 0:
        return 0, []

    # Score 0-100 from hit ratio; apply a modest +bias when the seed's suggested
    # category is a strong match too (never enough to override a clearly-matching
    # different category).
    ratio = best_hits / max(1, num_keywords)
    score = int(ratio * 100)

    if category and per_category_hits.get(category, 0) >= best_hits:
        score = min(100, score + 10)

    if score < MIN_SCORE:
        return 0, matches

    return score, matches[:12]


def extract_links_and_title(html: str, base_url: str) -> tuple[str | None, list[str]]:
    """Return (title, same-absolute-link list). Uses BeautifulSoup like extractor_html."""
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        title = h1.get_text(strip=True)

    links: list[str] = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        abs_url = urljoin(base_url, href)
        if abs_url in seen:
            continue
        seen.add(abs_url)
        links.append(abs_url)
    return title, links


def discover_sources(
    seed_url: str,
    max_depth: int = DEFAULT_MAX_DEPTH,
    suggested_category: str | None = None,
    session=None,
    fetch_fn=_default_fetch,
    min_delay_seconds: float = DEFAULT_MIN_DELAY_SECONDS,
) -> tuple[list[DiscoveredCandidate], dict]:
    """Discover candidate pages under `seed_url` and persist them (PENDING_REVIEW).

    Returns (created/updated candidates list, report dict). `session` must be a
    SQLAlchemy Session if persistence is desired; discovery also works in a pure
    read-only mode (session=None) but then results are not persisted.

    design: depth 0 is the seed page. Depth 1 = pages linked from it, etc.
    """
    report: dict = {"seed_url": seed_url, "crawled": [], "skipped": [], "errors": []}
    report["robots_applied"] = False
    report["robots_blocked_seed"] = False

    base_host = _host_of(seed_url)
    if not base_host:
        return [], report

    # 1. robots.txt gate
    gate = RobotsGate(seed_url, fetch_fn=fetch_fn)
    report["robots_applied"] = True
    if not gate.can_fetch(seed_url):
        report["robots_blocked_seed"] = True
        report["errors"].append(
            f"robots.txt disallows crawling {seed_url}; discovery aborted"
        )
        return [], report

    # 2. crawl BFS
    existing_urls = _existing_source_urls(session)
    visited: set[str] = set()
    current_depth = 0
    frontier: list[tuple[str, int]] = [(seed_url, 0)]
    candidates: list[DiscoveredCandidate] = []
    created: list[DiscoveredCandidate] = []

    while frontier and current_depth <= max_depth:
        next_frontier: list[tuple[str, int]] = []
        for url, depth in frontier:
            if url in visited:
                continue
            visited.add(url)

            if not gate.can_fetch(url):
                report["skipped"].append({"url": url, "reason": "robots disallow"})
                continue
            if not _is_same_domain(url, base_host):
                report["skipped"].append({"url": url, "reason": "different domain"})
                continue

            report["crawled"].append(url)
            res = fetch_fn(url, timeout=DEFAULT_TIMEOUT_SECONDS)
            if not res.ok or not res.raw_bytes:
                report["errors"].append({"url": url, "error": res.error or "fetch failed"})
                continue

            html = res.raw_bytes.decode("utf-8", errors="replace")
            title, links = extract_links_and_title(html, url)
            page_text = f"{title or ''} {url} " + _text_sample(html)

            score, matched = match_keywords(page_text, suggested_category)
            if score >= MIN_SCORE:
                candidate = DiscoveredCandidate(
                    candidate_url=url,
                    page_title=title,
                    matched_keywords=matched if score > 0 else [],
                    confidence_score=score,
                    suggested_visa_category=_resolve_category(page_text, suggested_category),
                )
                candidates.append(candidate)
                if session is not None and url not in existing_urls:
                    created.append(candidate)

            if depth < max_depth:
                for link in links:
                    next_frontier.append((link, depth + 1))

            if min_delay_seconds:
                time.sleep(min_delay_seconds)

        frontier = next_frontier
        current_depth += 1

    # 2b. optional LLM second opinion for borderline-scored candidates (graceful skip)
    if _llm_second_opinion_enabled():
        candidates = _apply_llm_second_opinion(candidates, suggested_category, report)

    # 3. persist
    if session is not None and created:
        repo = DiscoveredSourceRepo(session)
        run_id = None
        for c in created:
            existing = repo.get(_candidate_id(c.candidate_url))
            if existing is None:
                repo.create(**c.to_create_kwargs(seed_url, run_id))
        session.commit()

    report["candidates_found"] = len(candidates)
    report["candidates_created"] = len(created)
    return created if session is not None else candidates, report


def _llm_second_opinion_enabled() -> bool:
    """Opt-in only: SOURCE_DISCOVERY_USE_LLM=1 must be set. Default off so discovery
    never issues provider calls (and never fails when the LLM is unavailable)."""
    return os.environ.get("SOURCE_DISCOVERY_USE_LLM", "").strip() in ("1", "true", "True")


def _apply_llm_second_opinion(candidates, suggested_category, report):
    """Best-effort LLM refinement for borderline candidates (score inside the middle
    third of the range). Degrades gracefully: if a real LLM client cannot be obtained
    or the governance gate blocks/would raise, the keyword result is kept unchanged."""
    try:
        from extraction.llm_client import get_client
        from llm_governance import LLMGovernance

        # get_client is OpenAI-only and fails fast on missing key / bad model —
        # so reaching this line means a real provider client is available.
        client = get_client(visa_category=(suggested_category or "").lower())
    except Exception as e:  # noqa: BLE001 - graceful degradation is the contract
        report["llm_second_opinion"] = f"skipped (LLM unavailable: {e})"
        return candidates

    governance = LLMGovernance(budget=_discovery_budget())
    report["llm_second_opinion"] = "applied"

    for c in candidates:
        if not (BORDERLINE_LOW <= c.confidence_score <= BORDERLINE_HIGH):
            continue
        messages = [{
            "role": "user",
            "content": (
                f"Given this page (<title>: {c.page_title}; url: {c.candidate_url}; "
                f"matched keywords: {', '.join(c.matched_keywords)}), which AU visa "
                f"category fits best: SKILLED, WORK, STUDENT, VISITOR, or UNKNOWN? "
                f"Answer with a single token only. Do not fabricate — UNKNOWN if unsure."
            ),
        }]
        gate = governance.pre_request(key="discovery", model=client.model_name, messages=messages)
        if gate.get("blocked") or gate.get("cached"):
            # blocked (rate/budget) or cached-unknown: keep keyword result; do not
            # fabricate a confidence we don't have.
            continue
        try:
            raw = client.extract({"question": messages[0]["content"]})
            token = (raw or "").strip().upper()
            for cat in ("SKILLED", "WORK", "STUDENT", "VISITOR"):
                if cat in token:
                    # Only refine when the keyword classification was uncertain (None) or
                    # the LLM independently agrees; never override a confident keyword
                    # classification with a guessed token.
                    if c.suggested_visa_category is None:
                        c.suggested_visa_category = cat
                    break
        except Exception as e:  # noqa: BLE001
            report["llm_second_opinion"] = "partial error; kept keyword results"
            continue
    return candidates


def _discovery_budget():
    """Build the BudgetTracker for discovery, reading the USD limits from the environment.

    Honors LLM_DAILY_BUDGET_USD / LLM_MONTHLY_BUDGET_USD (falling back to the module
    defaults when unset) so operators can cap discovery spend without code changes.
    """
    from llm_governance import BudgetTracker

    def _usd(key: str) -> float | None:
        raw = os.environ.get(key)
        if raw is None or raw.strip() == "":
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    daily = _usd("LLM_DAILY_BUDGET_USD")
    monthly = _usd("LLM_MONTHLY_BUDGET_USD")
    return BudgetTracker(
        daily_limit_usd=(daily if daily is not None else 10.0),
        monthly_limit_usd=(monthly if monthly is not None else 200.0),
    )


def _resolve_category(page_text: str, suggested_category: str | None) -> str | None:
    """Do NOT invent a classification: return the strongest keyword category, or None
    when nothing clearly matches beyond a bare threshold. If the seed suggests a
    category (bias) and it is the top (or tied-top) match, respect it; otherwise pick
    the strongest unambiguous category; ambiguous ties resolve to None for the admin."""
    text_lower = (page_text or "").lower()
    per_cat = {
        cat: sum(1 for kw in CATEGORY_KEYWORDS[cat] if kw in text_lower)
        for cat in CATEGORY_KEYWORDS
    }
    ranked = sorted(per_cat.values(), reverse=True)
    if ranked[0] == 0:
        return None
    top_cats = [c for c, v in per_cat.items() if v == ranked[0]]
    # Tied top categories -> ambiguous -> leave for the admin unless the suggested
    # category is one of the tied leaders.
    if len(top_cats) > 1:
        if suggested_category and per_cat.get(suggested_category) == ranked[0]:
            return suggested_category
        return None
    best = top_cats[0]
    if suggested_category and per_cat.get(suggested_category, 0) == per_cat[best]:
        return suggested_category
    return best


def _text_sample(html: str, max_chars: int = 4000) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag_name in ("script", "style", "nav", "header", "footer", "noscript"):
        for tag in soup.find_all(tag_name):
            tag.decompose()
    text = soup.get_text(" ", strip=True)
    return text[:max_chars]


def _existing_source_urls(session):
    if session is None:
        return set()
    repo = SourceRepo(session)
    from sqlalchemy import select
    from db.models import GovernmentSource
    rows = list(session.execute(select(GovernmentSource.url)).scalars())
    return {u for u in rows if u}
