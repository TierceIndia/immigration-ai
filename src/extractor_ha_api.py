"""Extractor for Home Affairs content via their internal SharePoint REST API.

Instead of rendering JavaScript, this calls the same API endpoint the Angular app uses:
  POST /_layouts/15/api/Data.aspx/GetVisaMetaData

The API returns structured JSON with HTML content for each visa stream card, plus the full
page schema (overview, eligibility criteria, etc.) is embedded in the detail page's hidden field.

This avoids anti-bot detection and produces structured, clean content for Phase 2 extraction.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as dataclass_field
from html import unescape

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://immi.homeaffairs.gov.au"
API_PATH = "/_layouts/15/api/Data.aspx/GetVisaMetaData"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
}


_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u00a0]")
_MULTI_SPACE_RE = re.compile(r" {2,}")
_CONCAT_RE = re.compile(r"([a-z])([A-Z])")
_ORPHAN_PUNCT_RE = re.compile(r"([.;,]) ?([A-Z])")
_SPACE_BEFORE_PUNCT_RE = re.compile(r" +([.,;:!?])")
_KNOWN_JOINED_WORDS = {
    "readinformation": "read information",
    "ourhealth": "our health",
    "thecharacter": "the character",
    "donotapply": "do not apply",
    "confirmyou": "confirm you",
    "anywherein": "anywhere in",
    "eligiblerelatives": "eligible relatives",
    "informationon": "information on",
    "relevantlist": "relevant list",
    "apoints": "a points",
}


def _repair_mojibake(text: str) -> str:
    """Repair UTF-8 text decoded as Windows-1252, without touching valid Unicode."""
    if not any(marker in text for marker in ("â€", "â€™", "â€“", "â€”", "Â")):
        return text
    try:
        return text.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def strip_html(html_str: str) -> str:
    """Convert HTML to clean text.

    Uses ``separator=" "`` so adjacent inline elements (``<a>``, ``<span>``)
    are joined with a space instead of concatenating words.  Post-processes
    to fix camelCase joins, known concatenations, and orphaned punctuation
    from removed link tags.
    """
    soup = BeautifulSoup(html_str, "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    text = _repair_mojibake(text)
    text = _ZERO_WIDTH_RE.sub("", text)
    text = _CONCAT_RE.sub(r"\1 \2", text)
    for joined, separated in _KNOWN_JOINED_WORDS.items():
        text = re.sub(rf"\b{joined}\b", separated, text, flags=re.IGNORECASE)
    text = re.sub(r"\bSkill Select\b", "SkillSelect", text)
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    text = _ORPHAN_PUNCT_RE.sub(r"\1 \2", text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    return text.strip()


@dataclass
class ExtractionResult:
    title: str
    chunks: list[dict] = dataclass_field(default_factory=list)
    clean_text: str = ""


def _make_chunks_from_schema(schema: dict, card_data: dict) -> list[dict]:
    """Convert the page schema and card data into document chunks."""
    chunks = []
    chunk_id = 0

    def _add_chunk(heading, text, chunk_type="PARAGRAPH"):
        nonlocal chunk_id
        if text and text.strip():
            chunk_id += 1
            chunks.append({
                "chunk_id": chunk_id,
                "chunk_type": chunk_type,
                "heading_path": heading,
                "order_index": chunk_id,
                "raw_text": text.strip(),
                "page_number": 1,
            })

    # Card-level data (skip Stay/Cost/ProcessingTime — the schema Overview
    # provides the same content with richer context, avoiding duplicates)
    _add_chunk("Overview", strip_html(card_data.get("comments", "")))
    _add_chunk("Unique Features", strip_html(card_data.get("uniqueFeatures", "")))

    applicant = schema.get("applicant", {})

    # Overview section
    overview = applicant.get("overview", {})
    if overview:
        _add_chunk("Overview > You Must", strip_html(overview.get("notes", "")))
        _add_chunk("Overview > With This Visa", strip_html(overview.get("withThisVisa", "")))
        _add_chunk("Overview > Visa Stay", strip_html(overview.get("visaStay", "")))
        _add_chunk("Overview > Visa Cost", strip_html(overview.get("visaCost", "")))
        _add_chunk("Overview > Processing Time", strip_html(overview.get("visaProcessingTime", "")))

    # About this visa section
    about = applicant.get("aboutThisVisa", {})
    if about:
        for item in about.get("about", []):
            if isinstance(item, dict):
                title = item.get("title", "")
                desc = item.get("description", "")
                _add_chunk(f"About > {title}", strip_html(desc))
            elif isinstance(item, str):
                _add_chunk("About", strip_html(item))

    # Eligibility section - this is the most important for requirement extraction
    eligibility = applicant.get("eligibility", {})
    if eligibility:
        criteria_list = eligibility.get("criteria", [])
        for criterion in criteria_list:
            if isinstance(criterion, dict):
                title = criterion.get("title", "")
                desc = criterion.get("description", "")
                _add_chunk(f"Eligibility > {title}", strip_html(desc), "PARAGRAPH")

                # Sub-criteria
                sub_criteria = criterion.get("criteria", [])
                for sub in sub_criteria:
                    if isinstance(sub, dict):
                        sub_title = sub.get("title", "")
                        sub_desc = sub.get("description", "")
                        _add_chunk(f"Eligibility > {title} > {sub_title}", strip_html(sub_desc))

    # Step by step section
    step_by_step = applicant.get("stepByStep", {})
    if step_by_step:
        steps = step_by_step.get("steps", [])
        for step in steps:
            if isinstance(step, dict):
                title = step.get("title", "")
                desc = step.get("description", "")
                _add_chunk(f"Step by Step > {title}", strip_html(desc))

    # When you have this visa section
    when = applicant.get("whenYouHaveThisVisa", {})
    if when:
        for key, val in when.items():
            if isinstance(val, str) and val.strip():
                _add_chunk(f"When You Have This Visa > {key}", strip_html(val))
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        title = item.get("title", "")
                        desc = item.get("description", "")
                        _add_chunk(f"When You Have This Visa > {title}", strip_html(desc))

    return chunks


def extract_homeaffairs_api(
    card_ids: list[str],
    web_url: str = "/Visa-subsite",
    timeout: int = 30,
) -> ExtractionResult:
    """Extract visa content from Home Affairs via their internal API.

    Args:
        card_ids: List of card IDs (e.g. ["189-63"] for points-tested stream)
        web_url: SharePoint web URL (default: /Visa-subsite)
        timeout: Request timeout in seconds

    Returns:
        ExtractionResult with title, chunks, and clean_text
    """
    # Step 1: Get card data from API
    resp = requests.post(
        f"{BASE_URL}{API_PATH}",
        json={"webUrl": web_url, "productCode": card_ids, "pageID": None},
        headers=HEADERS,
        timeout=timeout,
    )
    resp.raise_for_status()
    cards = resp.json()["d"]["data"]

    if not cards:
        return ExtractionResult(title="No data", chunks=[], clean_text="")

    all_chunks = []
    all_text_parts = []
    title = cards[0].get("visaSubclassHeading", cards[0].get("title", ""))

    for card in cards:
        # Step 2: Fetch the detail page to get the full schema
        detail_url = card.get("path", "")
        if detail_url:
            try:
                detail_resp = requests.get(detail_url, headers=HEADERS, timeout=timeout)
                detail_resp.raise_for_status()

                match = re.search(
                    r'PageSchemaHiddenField.*?value="([^"]*)"',
                    detail_resp.text,
                    re.DOTALL,
                )
                if match:
                    schema = json.loads(unescape(match.group(1)))
                else:
                    schema = {}
            except Exception:
                schema = {}
        else:
            schema = {}

        # Step 3: Build chunks from card data + schema
        chunks = _make_chunks_from_schema(schema, card)
        for c in chunks:
            c["card_id"] = card.get("subClass", "")
            c["card_title"] = card.get("title", "")
        all_chunks.extend(chunks)

    # Build clean text
    for c in all_chunks:
        all_text_parts.append(f"[{c['heading_path']}]\n{c['raw_text']}")
    clean_text = "\n\n".join(all_text_parts)

    return ExtractionResult(title=title, chunks=all_chunks, clean_text=clean_text)


if __name__ == "__main__":
    result = extract_homeaffairs_api(["189-63"])
    print(f"Title: {result.title}")
    print(f"Chunks: {len(result.chunks)}")
    for c in result.chunks[:10]:
        print(f"  [{c['heading_path']}] {c['raw_text'][:100]}...")
