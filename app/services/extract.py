"""Text extraction from uploaded files (PDF, DOCX, TXT, MD, CSV)."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

ALLOWED_EXTENSIONS = {".txt", ".md", ".csv", ".pdf", ".docx"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


def extract_text(filename: str, content: bytes) -> str:
    """Extract plain text from file bytes based on the file extension.

    Args:
        filename: Original filename (used to determine type).
        content: Raw file bytes.

    Returns:
        Extracted text as a string.

    Raises:
        ValueError: If the file extension is not supported.
    """
    ext = Path(filename).suffix.lower()

    if ext in {".txt", ".md", ".csv"}:
        return content.decode("utf-8")

    if ext == ".pdf":
        return _extract_pdf(content)

    if ext == ".docx":
        return _extract_docx(content)

    raise ValueError(f"Unsupported file type: {ext}")


def _extract_pdf(content: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(content))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages)


def _extract_docx(content: bytes) -> str:
    from docx import Document

    doc = Document(BytesIO(content))
    return "\n".join(p.text for p in doc.paragraphs)
