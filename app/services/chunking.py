"""Text normalization and chunking service."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass
class TextChunk:
    """A chunk of text with its position index."""
    index: int
    content: str
    char_count: int


def normalize_text(text: str) -> str:
    """Normalize unicode, collapse whitespace, strip control characters."""
    # Normalize unicode to NFC form
    text = unicodedata.normalize("NFC", text)
    # Remove control characters (except newlines and tabs)
    text = re.sub(r"[^\S \n\t]+", "", text)
    # Collapse multiple blank lines into at most two newlines
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse multiple spaces/tabs into a single space
    text = re.sub(r"[^\S\n]+", " ", text)
    # Strip trailing/leading spaces on each line
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


# Separators ordered by preference — try to split on semantic boundaries first
_SEPARATORS = [
    "\n\n",   # paragraph breaks
    "\n",     # line breaks
    ". ",     # sentence boundaries
    "? ",
    "! ",
    "; ",
    ", ",
    " ",      # word boundaries
    "",       # character-level fallback
]


def chunk_text(
    text: str,
    chunk_size: int = 512,
    chunk_overlap: int = 64,
    separators: list[str] | None = None,
) -> list[TextChunk]:
    """Split text into overlapping chunks using recursive character splitting.

    Args:
        text: The input text to chunk.
        chunk_size: Target maximum characters per chunk.
        chunk_overlap: Number of overlapping characters between chunks.
        separators: Ordered list of separators to try. Defaults to paragraph → word.

    Returns:
        List of TextChunk objects with content and positional metadata.
    """
    text = normalize_text(text)
    if not text:
        return []

    if len(text) <= chunk_size:
        return [TextChunk(index=0, content=text, char_count=len(text))]

    seps = separators or _SEPARATORS
    splits = _recursive_split(text, seps, chunk_size)

    # Merge small splits and enforce overlap
    chunks: list[TextChunk] = []
    current = ""

    for split in splits:
        candidate = f"{current} {split}".strip() if current else split

        if len(candidate) <= chunk_size:
            current = candidate
        else:
            if current:
                chunks.append(TextChunk(
                    index=len(chunks),
                    content=current,
                    char_count=len(current),
                ))
                # Start next chunk with overlap from the end of the current chunk
                if chunk_overlap > 0 and len(current) > chunk_overlap:
                    current = current[-chunk_overlap:] + " " + split
                else:
                    current = split
            else:
                # Single split exceeds chunk_size — force emit it
                chunks.append(TextChunk(
                    index=len(chunks),
                    content=split[:chunk_size],
                    char_count=min(len(split), chunk_size),
                ))
                current = split[chunk_size:] if len(split) > chunk_size else ""

    if current.strip():
        chunks.append(TextChunk(
            index=len(chunks),
            content=current.strip(),
            char_count=len(current.strip()),
        ))

    return chunks


def _recursive_split(text: str, separators: list[str], chunk_size: int) -> list[str]:
    """Recursively split text using the first separator that produces segments."""
    if not separators:
        return [text]

    sep = separators[0]
    remaining_seps = separators[1:]

    if sep == "":
        # Character-level split
        return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]

    parts = text.split(sep)

    result: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part) <= chunk_size:
            result.append(part)
        else:
            # This part is still too large — recurse with the next separator
            result.extend(_recursive_split(part, remaining_seps, chunk_size))

    return result
