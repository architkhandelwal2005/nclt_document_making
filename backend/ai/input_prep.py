"""Conservative token estimation and page-preserving request planning."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any


@dataclass(frozen=True)
class InputPlan:
    document_text: str
    estimated_document_tokens: int
    chunks: tuple[str, ...]
    chunking_required: bool
    page_ranges: tuple[tuple[int, int], ...]


def estimate_tokens(text: str) -> int:
    """Estimate English legal-document tokens conservatively at four characters each."""
    return ceil(len(text) / 4)


def _page_sections(extracted: dict[str, Any]) -> list[tuple[int, str]]:
    sections = []
    for index, page in enumerate(extracted.get("document", {}).get("pages", []), 1):
        number = int(page.get("number") or index)
        body = "\n".join(
            str(block.get("text") or "").strip()
            for block in page.get("blocks", [])
            if str(block.get("text") or "").strip()
        )
        sections.append((number, f"--- PAGE {number} ---\n{body}"))
    return sections


def plan_inputs(extracted: dict[str, Any], *, provider: str, direct_token_limit: int,
                chunk_target_tokens: int) -> InputPlan:
    pages = _page_sections(extracted)
    document_text = "\n\n".join(text for _, text in pages)
    estimated = estimate_tokens(document_text)
    if provider != "groq" or estimated <= direct_token_limit:
        page_range = ((pages[0][0], pages[-1][0]),) if pages else tuple()
        return InputPlan(document_text, estimated, (document_text,) if document_text else tuple(), False, page_range)

    chunks: list[str] = []
    ranges: list[tuple[int, int]] = []
    current: list[tuple[int, str]] = []
    for page in pages:
        proposed = "\n\n".join(text for _, text in [*current, page])
        if current and estimate_tokens(proposed) > chunk_target_tokens:
            chunks.append("\n\n".join(text for _, text in current))
            ranges.append((current[0][0], current[-1][0]))
            current = [page]
        else:
            current.append(page)
    if current:
        chunks.append("\n\n".join(text for _, text in current))
        ranges.append((current[0][0], current[-1][0]))
    return InputPlan(document_text, estimated, tuple(chunks), len(chunks) > 1, tuple(ranges))
