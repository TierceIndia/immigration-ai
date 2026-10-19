"""HTML -> structured clean text.

Extracts headings / paragraphs / lists / tables from the main content area of a
Home Affairs / legislation.gov.au page, discarding nav, header, footer, cookie
banners, and sidebars. Preserves heading hierarchy (heading_path) because that's
what makes evidence traceable later ("this requirement came from the 'Eligibility'
section").

No requirement/rule classification happens here - that is Phase-2's job. Chunks are
purely structural.

JSON-hydration fallback (see _find_json_hydration_fragments): several
immi.homeaffairs.gov.au page templates (e.g. a "visa-listing" overview page
hosting an Angular <ha-streams-root> widget) ship their real content as an
HTML-escaped JSON blob inside a hidden <input>'s value attribute, and only
render it into real <p>/<ul>/<table> DOM via client-side JS. Evidence: the
same SharePoint CMS serves a different response depending on its own
server-side bot-detection (an `ms-isBot` class on <html>) - when it detects a
bot/crawler it appears to server-render this same JSON into real static HTML
(confirmed on SRC-AU-189-POINTS-TABLE and SRC-AU-SKILLSELECT-EOI's real saved
captures); when it doesn't, the client gets only the inert JSON blob and the
widget never renders in a plain headless browser session within a reasonable
wait. We do not attempt to influence that server-side classification (that
would be fingerprint evasion) - instead, when the normal DOM walk below finds
nothing, we fall back to reading the exact same real government text straight
out of the JSON the server already sent us. Confirmed two different JSON
shapes across page templates (a flat {title,description,eligibility,...} shape
and a {content:[{text,block}]} tab/card-array shape), so the fallback walks
the JSON structurally rather than hardcoding one shape's key names.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

# "form" is deliberately NOT in this list. Some government pages (e.g. an ASP.NET
# CMS page hosting an embedded Angular content widget, seen on
# immi.homeaffairs.gov.au/visas/.../visitor-600) wrap the entire real page body -
# headings, paragraphs, lists - in a top-level <form id="aspnetForm"> postback
# wrapper. Decomposing every <form> outright (as this used to do) deleted that
# whole page's content, producing 0 chunks from a page that fetched successfully
# and genuinely has content. Form *controls* (input/button/select/textarea/label)
# are never extracted anyway - the chunk walk below only looks at
# headings/p/table/ul/ol - so unwrapping (not decomposing) forms below is safe:
# it drops the <form> wrapper itself while keeping any real content nested inside.
NOISE_TAGS = ["script", "style", "nav", "header", "footer", "noscript", "svg"]
NOISE_SELECTORS = [
    "[class*=cookie]", "[id*=cookie]",
    "[class*=skip-link]",
    "[class*=breadcrumb]",
    "[class*=sidebar]",
    "[role=navigation]",
    "[role=banner]",
    "[role=contentinfo]",
]

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

# document_chunks.heading_path is VARCHAR(512). A real legislation page can
# nest long headings deep enough to exceed that, and Postgres rejects the
# insert where SQLite silently accepts it - so the path is bounded here, at
# the one place that builds it. The *deepest* headings are kept because they
# carry the specific context ("... > Age" beats "Eligibility > ..." when
# something has to go), with a leading marker so a truncated path is never
# mistaken for a complete one.
MAX_HEADING_PATH_CHARS = 512
HEADING_PATH_SEPARATOR = " > "
TRUNCATION_MARKER = "..."


@dataclass
class Chunk:
    chunk_type: str  # HEADING | PARAGRAPH | TABLE | LIST
    heading_path: str
    order_index: int
    raw_text: str
    page_number: int | None = None


@dataclass
class ExtractionResult:
    title: str | None
    chunks: list[Chunk] = field(default_factory=list)
    clean_text: str = ""


def _clean_soup(soup: BeautifulSoup) -> BeautifulSoup:
    for tag_name in NOISE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()
    for selector in NOISE_SELECTORS:
        for tag in soup.select(selector):
            tag.decompose()
    for form in soup.find_all("form"):
        form.unwrap()
    return soup


def _find_main_content(soup: BeautifulSoup):
    # Prefer a semantic <main>, then common CMS content containers, then fall back to <body>.
    candidates = [
        soup.find("main"),
        soup.find(attrs={"role": "main"}),
        soup.find(id="content"),
        soup.find(id="main-content"),
        soup.find(class_=lambda c: c and "content" in c and "field" not in c),
        soup.body,
    ]
    for c in candidates:
        if c is not None:
            return c
    return soup


def _table_to_text(table_tag) -> str:
    rows = []
    for tr in table_tag.find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _list_to_text(list_tag) -> str:
    items = [li.get_text(strip=True) for li in list_tag.find_all("li", recursive=False)]
    return "\n".join(f"- {i}" for i in items if i)


def _heading_path(heading_stack: list[str]) -> str:
    """Join a heading stack into "h1 > h2 > h3", bounded to the column width."""
    path = HEADING_PATH_SEPARATOR.join(heading_stack)
    if len(path) <= MAX_HEADING_PATH_CHARS:
        return path

    # Keep as many of the deepest headings as fit, then mark the cut.
    kept: list[str] = []
    budget = MAX_HEADING_PATH_CHARS - len(TRUNCATION_MARKER) - len(HEADING_PATH_SEPARATOR)
    for heading in reversed(heading_stack):
        cost = len(heading) + (len(HEADING_PATH_SEPARATOR) if kept else 0)
        if cost > budget:
            break
        kept.insert(0, heading)
        budget -= cost
    if not kept:
        # One heading alone is longer than the whole budget: hard-cut it.
        only = heading_stack[-1][: MAX_HEADING_PATH_CHARS - len(TRUNCATION_MARKER)]
        return f"{TRUNCATION_MARKER}{only}"
    return HEADING_PATH_SEPARATOR.join([TRUNCATION_MARKER] + kept)


def _walk_blocks(container, heading_stack: list[str], order_index: int):
    """Walk block-level elements in document order within `container`, starting
    from the given heading_stack/order_index (so callers can continue a single
    running order across multiple containers). Returns (chunks, text_parts,
    new_order_index).
    """
    chunks: list[Chunk] = []
    text_parts: list[str] = []
    block_tags = list(HEADING_TAGS) + ["p", "table", "ul", "ol"]
    for el in container.find_all(block_tags):
        # Skip nested tables/lists inside already-processed tables/lists
        if el.find_parent(["table"]) and el.name != "table":
            continue
        if el.name in HEADING_TAGS:
            level = int(el.name[1])
            text = el.get_text(strip=True)
            if not text:
                continue
            # Trim heading_stack to this level, then push
            heading_stack = heading_stack[: level - 1]
            heading_stack.append(text)
            heading_path = _heading_path(heading_stack)
            order_index += 1
            chunks.append(Chunk("HEADING", heading_path, order_index, text))
            text_parts.append(text)
        elif el.name == "p":
            text = el.get_text(strip=True)
            if not text or len(text) < 2:
                continue
            heading_path = _heading_path(heading_stack)
            order_index += 1
            chunks.append(Chunk("PARAGRAPH", heading_path, order_index, text))
            text_parts.append(text)
        elif el.name == "table":
            text = _table_to_text(el)
            if not text:
                continue
            heading_path = _heading_path(heading_stack)
            order_index += 1
            chunks.append(Chunk("TABLE", heading_path, order_index, text))
            text_parts.append(text)
        elif el.name in ("ul", "ol"):
            if el.find_parent(["ul", "ol"]):
                continue  # avoid double-counting nested lists
            text = _list_to_text(el)
            if not text:
                continue
            heading_path = _heading_path(heading_stack)
            order_index += 1
            chunks.append(Chunk("LIST", heading_path, order_index, text))
            text_parts.append(text)
    return chunks, text_parts, order_index


_HTML_FRAGMENT_RE = re.compile(r"^\s*<(p|ul|ol|table|h[1-6]|div)\b", re.IGNORECASE)
# Keys that typically carry a human-readable label for a sibling content field
# (e.g. {"text": "Overview", "block": "<p>...</p>"}).
_LABEL_KEYS = ("text", "title", "label", "name", "heading")


def _looks_like_html_fragment(value: str) -> bool:
    return bool(_HTML_FRAGMENT_RE.match(value or ""))


def _humanize(key: str) -> str:
    if not key:
        return "Content"
    return key[:1].upper() + key[1:]


def _walk_json_for_html(value, label_hint: str | None = None) -> list[tuple[str, str]]:
    """Recursively walk a parsed JSON value, yielding (label, html_fragment)
    pairs for every string leaf that looks like an HTML fragment.

    Handles both observed shapes without hardcoding either: a flat dict of
    named HTML fields (`{"eligibility": "<p>...</p>"}`) and a list of labelled
    blocks (`{"content": [{"text": "Overview", "block": "<p>...</p>"}]}`) - in
    the latter, a sibling "text"/"title"/"label" field in the same dict is
    used as the label for the HTML-bearing field next to it.
    """
    results: list[tuple[str, str]] = []
    if isinstance(value, dict):
        sibling_label = None
        for key in _LABEL_KEYS:
            v = value.get(key)
            if isinstance(v, str) and v.strip() and not _looks_like_html_fragment(v):
                sibling_label = v.strip()
                break
        for key, v in value.items():
            if isinstance(v, str):
                if _looks_like_html_fragment(v):
                    results.append((sibling_label or _humanize(key), v))
            else:
                results.extend(
                    _walk_json_for_html(v, label_hint=sibling_label or _humanize(key))
                )
    elif isinstance(value, list):
        for item in value:
            results.extend(_walk_json_for_html(item, label_hint=label_hint))
    elif isinstance(value, str):
        if _looks_like_html_fragment(value):
            results.append((label_hint or "Content", value))
    return results


def _find_json_hydration_fragments(container) -> list[tuple[str, str]]:
    """Find hidden <input> elements whose value is JSON, and pull out any real
    HTML-fragment string values from inside. See the module docstring for why
    this exists: some pages only ever materialise this content into visible
    DOM via client-side JS that doesn't run in our fetch session.

    `container` must be the same main-content element the real DOM walk uses
    (not the whole document): the content widget's hydration data lives in
    the same region as the widget itself (confirmed on the real capture this
    fallback was built from - the widget root and its data's common ancestor
    is 2 levels up, inside <main>), and site-wide chrome outside that region
    (header/footer/nav widgets: cookie banners, search, language pickers) can
    carry their own unrelated JSON with incidental HTML-looking strings that
    have nothing to do with this page's actual content. Scoping to the same
    container the real walk already trusts avoids pulling those in.
    """
    fragments: list[tuple[str, str]] = []
    for inp in container.find_all("input", attrs={"type": "hidden"}):
        raw_value = inp.get("value")
        if not raw_value or "{" not in raw_value:
            continue
        try:
            parsed = json.loads(raw_value)
        except (ValueError, TypeError):
            continue
        fragments.extend(_walk_json_for_html(parsed))
    return fragments


def extract_html(raw_bytes: bytes, source_url: str) -> ExtractionResult:
    soup = BeautifulSoup(raw_bytes, "html.parser")
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None
    # Prefer h1 as title if present and title tag looks like boilerplate
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        title = h1.get_text(strip=True)

    cleaned = _clean_soup(soup)
    main = _find_main_content(cleaned)

    chunks, text_parts, order_index = _walk_blocks(main, [], 0)

    if not chunks:
        # The normal DOM walk found nothing real - before concluding this page
        # is genuinely contentless, check whether the real content is sitting
        # inert in a JSON hydration blob (see module docstring).
        for label, fragment_html in _find_json_hydration_fragments(main):
            fragment_soup = BeautifulSoup(fragment_html, "html.parser")
            frag_chunks, frag_text, order_index = _walk_blocks(
                fragment_soup, [label], order_index
            )
            chunks.extend(frag_chunks)
            text_parts.extend(frag_text)

    clean_text = "\n\n".join(text_parts)
    return ExtractionResult(title=title, chunks=chunks, clean_text=clean_text)
