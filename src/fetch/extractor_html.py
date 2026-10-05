"""HTML -> structured clean text.

Extracts headings / paragraphs / lists / tables from the main content area of a
Home Affairs / legislation.gov.au page, discarding nav, header, footer, cookie
banners, and sidebars. Preserves heading hierarchy (heading_path) because that's
what makes evidence traceable later ("this requirement came from the 'Eligibility'
section").

No requirement/rule classification happens here - that is Phase-2's job. Chunks are
purely structural.
"""
from __future__ import annotations

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


def extract_html(raw_bytes: bytes, source_url: str) -> ExtractionResult:
    soup = BeautifulSoup(raw_bytes, "html.parser")
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None
    # Prefer h1 as title if present and title tag looks like boilerplate
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        title = h1.get_text(strip=True)

    soup = _clean_soup(soup)
    main = _find_main_content(soup)

    chunks: list[Chunk] = []
    heading_stack: list[str] = []
    order_index = 0
    text_parts: list[str] = []

    # Walk block-level elements in document order within main content.
    block_tags = list(HEADING_TAGS) + ["p", "table", "ul", "ol"]
    for el in main.find_all(block_tags):
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
            heading_path = " > ".join(heading_stack)
            order_index += 1
            chunks.append(Chunk("HEADING", heading_path, order_index, text))
            text_parts.append(text)
        elif el.name == "p":
            text = el.get_text(strip=True)
            if not text or len(text) < 2:
                continue
            heading_path = " > ".join(heading_stack)
            order_index += 1
            chunks.append(Chunk("PARAGRAPH", heading_path, order_index, text))
            text_parts.append(text)
        elif el.name == "table":
            text = _table_to_text(el)
            if not text:
                continue
            heading_path = " > ".join(heading_stack)
            order_index += 1
            chunks.append(Chunk("TABLE", heading_path, order_index, text))
            text_parts.append(text)
        elif el.name in ("ul", "ol"):
            if el.find_parent(["ul", "ol"]):
                continue  # avoid double-counting nested lists
            text = _list_to_text(el)
            if not text:
                continue
            heading_path = " > ".join(heading_stack)
            order_index += 1
            chunks.append(Chunk("LIST", heading_path, order_index, text))
            text_parts.append(text)

    clean_text = "\n\n".join(text_parts)
    return ExtractionResult(title=title, chunks=chunks, clean_text=clean_text)
