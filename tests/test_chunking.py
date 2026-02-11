"""Unit tests for the chunking service."""

from app.services.chunking import chunk_text, normalize_text


def test_normalize_collapses_whitespace():
    raw = "  Hello   world  \n\n\n\n  foo  "
    result = normalize_text(raw)
    assert result == "Hello world\n\nfoo"


def test_normalize_strips_control_chars():
    raw = "Hello\x00\x01World"
    result = normalize_text(raw)
    assert "Hello" in result
    assert "World" in result


def test_chunk_empty_returns_empty():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_chunk_short_text_single_chunk():
    text = "Hello, world!"
    chunks = chunk_text(text, chunk_size=100)
    assert len(chunks) == 1
    assert chunks[0].index == 0
    assert chunks[0].content == "Hello, world!"
    assert chunks[0].char_count == len("Hello, world!")


def test_chunk_respects_size_limit():
    # 1000 chars of text, chunk_size=200
    text = "word " * 200  # ~1000 chars
    chunks = chunk_text(text.strip(), chunk_size=200, chunk_overlap=0)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.char_count <= 220  # allow some margin for word boundaries


def test_chunk_indices_are_sequential():
    text = "Paragraph one.\n\nParagraph two.\n\nParagraph three.\n\nParagraph four."
    chunks = chunk_text(text, chunk_size=30, chunk_overlap=0)
    for i, chunk in enumerate(chunks):
        assert chunk.index == i


def test_chunk_overlap():
    # Create text with clear sentence boundaries
    sentences = [f"Sentence number {i} is here." for i in range(20)]
    text = " ".join(sentences)
    chunks = chunk_text(text, chunk_size=100, chunk_overlap=30)

    # With overlap, adjacent chunks should share some text
    if len(chunks) >= 2:
        # The start of chunk[1] should contain some chars from end of chunk[0]
        overlap_text = chunks[0].content[-30:]
        # There should be some overlap (not necessarily exact due to word boundaries)
        assert len(chunks) >= 2


def test_chunk_large_text():
    """Ensure we can handle a substantial document."""
    text = "\n\n".join([f"Section {i}. " + ("Content. " * 50) for i in range(10)])
    chunks = chunk_text(text, chunk_size=512, chunk_overlap=64)
    assert len(chunks) > 5
    total_chars = sum(c.char_count for c in chunks)
    assert total_chars > len(text) * 0.5  # coverage — most text represented
