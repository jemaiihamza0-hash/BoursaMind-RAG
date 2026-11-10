"""Generic document chunking with stable token estimates and page anchors."""

import re
from dataclasses import dataclass

_HEADING_RE = re.compile(
    r"^(#{1,6}\s+.+|\d+(\.\d+)*\s+\S.+|[A-Z][A-Z0-9 \-/]{4,80})$"
)


@dataclass
class Chunk:
    index: int
    content: str
    token_count: int
    page_number: int | None = None


@dataclass
class _Unit:
    text: str
    page: int | None


def estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


def split_pages(text: str | None) -> list[str] | None:
    if not text:
        return None
    marker = "\f"
    if marker not in text:
        return None
    return [page.strip() for page in text.split(marker)]


def _split_blocks(text: str) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    blocks: list[str] = []
    pending_heading: str | None = None
    for para in paragraphs:
        first_line = para.splitlines()[0].strip()
        is_heading = len(para.splitlines()) == 1 and bool(_HEADING_RE.match(first_line))
        if is_heading:
            pending_heading = para
            continue
        if pending_heading:
            blocks.append(f"{pending_heading}\n{para}")
            pending_heading = None
        else:
            blocks.append(para)
    if pending_heading:
        blocks.append(pending_heading)
    return blocks


def _split_long_block(block: str, target_tokens: int) -> list[str]:
    if estimate_tokens(block) <= target_tokens:
        return [block]
    sentences = re.split(r"(?<=[.!?])\s+", block)
    out: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if current and estimate_tokens(candidate) > target_tokens:
            out.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        out.append(current)
    return out


def chunk_text(
    text: str,
    *,
    target_tokens: int = 700,
    overlap_tokens: int = 120,
    min_tokens: int = 80,
    pages: list[str] | None = None,
) -> list[Chunk]:
    text = (text or "").strip()
    if not text and not pages:
        return []

    units: list[_Unit] = []
    if pages:
        for page_no, page_text in enumerate(pages, start=1):
            for block in _split_blocks((page_text or "").strip()):
                units.extend(
                    _Unit(part, page_no)
                    for part in _split_long_block(block, target_tokens)
                )
    else:
        for block in _split_blocks(text):
            units.extend(_Unit(part, None) for part in _split_long_block(block, target_tokens))

    # A citation names one page, so when page boundaries are known a chunk never
    # crosses one: each page is windowed on its own (overlap and the small-tail
    # merge stay inside the page). Previously a chunk took the page of its first
    # unit while overlap carried earlier pages forward, so figures printed on
    # pages 4 and 25 were cited as "page 2".
    groups: list[list[_Unit]] = []
    for unit in units:
        if pages and groups and groups[-1][0].page == unit.page:
            groups[-1].append(unit)
        elif pages or not groups:
            groups.append([unit])
        else:
            groups[-1].append(unit)

    chunks: list[tuple[str, int | None]] = []
    for group in groups:
        group_chunks: list[tuple[str, int | None]] = []
        current: list[_Unit] = []
        current_tokens = 0
        for unit in group:
            unit_tokens = estimate_tokens(unit.text)
            if current and current_tokens + unit_tokens > target_tokens:
                group_chunks.append(_join(current))
                current, current_tokens = _overlap_tail(current, overlap_tokens)
            current.append(unit)
            current_tokens += unit_tokens

        if current:
            content, page = _join(current)
            if group_chunks and estimate_tokens(content) < min_tokens:
                previous, previous_page = group_chunks[-1]
                group_chunks[-1] = (f"{previous}\n\n{content}", previous_page)
            else:
                group_chunks.append((content, page))
        chunks.extend(group_chunks)

    return [
        Chunk(index=i, content=content, token_count=estimate_tokens(content), page_number=page)
        for i, (content, page) in enumerate(chunks)
    ]


def _join(units: list[_Unit]) -> tuple[str, int | None]:
    return "\n\n".join(u.text for u in units), units[0].page if units else None


def _overlap_tail(units: list[_Unit], overlap_tokens: int) -> tuple[list[_Unit], int]:
    tail: list[_Unit] = []
    total = 0
    for unit in reversed(units):
        unit_tokens = estimate_tokens(unit.text)
        if total + unit_tokens > overlap_tokens and tail:
            break
        tail.insert(0, unit)
        total += unit_tokens
    return tail, total
