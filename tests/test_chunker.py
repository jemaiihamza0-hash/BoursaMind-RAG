"""Page anchors: a citation's page must be the page its passage is printed on."""

from app.chunking.chunker import chunk_text, split_pages


def _paragraphs(tag: str, count: int) -> str:
    return "\n\n".join(
        f"{tag} paragraph {i} " + "valeur financiere publiee " * 25 for i in range(count)
    )


def test_chunks_never_span_pages_and_carry_their_own_page():
    text = "\f".join([_paragraphs("PAGEONE", 6), "", _paragraphs("PAGETHREE", 2)])
    chunks = chunk_text(text, pages=split_pages(text), target_tokens=300, overlap_tokens=60)

    assert chunks
    for chunk in chunks:
        assert not ("PAGEONE" in chunk.content and "PAGETHREE" in chunk.content)
        expected = 1 if "PAGEONE" in chunk.content else 3
        assert chunk.page_number == expected
    assert {chunk.page_number for chunk in chunks} == {1, 3}


def test_boursamind_figure_lines_are_cited_on_their_pages():
    """BoursaMind sends approved figures as one short block per page.

    Seen on real BIAT FY2025 statements: total assets (page 2), net income
    (page 4) and the share count (page 25) were merged into one chunk cited
    as page 2.
    """
    pages = [""] * 25
    pages[1] = "BIAT · TOTAL_ASSETS · 2025 · 26606988000.0000 TND"
    pages[3] = "BIAT · NET_INCOME · 2025 · 385264000.0000 TND"
    pages[24] = "BIAT · SHARES_OUTSTANDING · 2025 · 40800000"
    text = "\f".join(pages) + "\f"
    cited = {
        chunk.content.split(" · ")[1]: chunk.page_number
        for chunk in chunk_text(text, pages=split_pages(text))
    }
    assert cited == {"TOTAL_ASSETS": 2, "NET_INCOME": 4, "SHARES_OUTSTANDING": 25}


def test_unpaged_text_keeps_overlapping_windows():
    text = _paragraphs("FLAT", 8)
    chunks = chunk_text(text, target_tokens=300, overlap_tokens=60)
    assert len(chunks) > 1
    assert all(chunk.page_number is None for chunk in chunks)
    # Overlap: consecutive chunks share at least one paragraph.
    assert any(
        set(a.content.split("\n\n")) & set(b.content.split("\n\n"))
        for a, b in zip(chunks, chunks[1:], strict=False)
    )
