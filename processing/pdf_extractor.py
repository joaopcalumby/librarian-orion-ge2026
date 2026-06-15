from __future__ import annotations

import io
import logging
import re
from collections import Counter
from typing import Literal

import fitz
from docling.datamodel.base_models import DocumentStream
from docling.document_converter import DocumentConverter

logger = logging.getLogger(__name__)

Extractor = Literal["docling", "pymupdf"]


class PdfExtractionError(Exception):
    """Erro durante extração; o orquestrador captura e marca o paper como falho."""


_REFERENCES_HEADING = re.compile(
    r"^\s{0,3}#{0,6}\s*(References|Referências|Bibliography|Works Cited)\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_PAGE_NUMBER_LINE = re.compile(r"^\s*(?:page\s+)?\d{1,4}\s*$", re.IGNORECASE)

_DEHYPHENATE = re.compile(r"(\w)-\n(\w)")


_docling_converter: DocumentConverter | None = None


def _get_docling() -> DocumentConverter:
    global _docling_converter
    if _docling_converter is None:
        logger.info("Inicializando DocumentConverter (primeira execução baixa modelos).")
        _docling_converter = DocumentConverter()
    return _docling_converter


def _extract_pages_pymupdf(pdf_bytes: bytes) -> list[str]:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        raise PdfExtractionError(f"PyMuPDF falhou ao abrir o PDF: {e}") from e

    pages: list[str] = []
    try:
        for page in doc:
            pages.append(page.get_text("text"))
    finally:
        doc.close()
    return pages


def _extract_markdown_docling(pdf_bytes: bytes) -> str:
    converter = _get_docling()
    stream = DocumentStream(name="paper.pdf", stream=io.BytesIO(pdf_bytes))
    try:
        result = converter.convert(stream)
    except Exception as e:
        raise PdfExtractionError(f"Docling falhou ao converter o PDF: {e}") from e
    return result.document.export_to_markdown()


def _strip_repeated_headers_footers(pages: list[str], threshold: float = 0.5) -> list[str]:
    if len(pages) < 3:
        return pages

    tops: list[str] = []
    bottoms: list[str] = []
    for raw in pages:
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if not lines:
            continue
        tops.append(lines[0])
        bottoms.append(lines[-1])

    min_count = max(2, int(len(pages) * threshold))
    common_tops = {ln for ln, c in Counter(tops).items() if c >= min_count}
    common_bottoms = {ln for ln, c in Counter(bottoms).items() if c >= min_count}

    cleaned: list[str] = []
    for raw in pages:
        lines = raw.splitlines()
        i, j = 0, len(lines)
        while i < j and (not lines[i].strip() or lines[i].strip() in common_tops):
            i += 1
        while j > i and (not lines[j - 1].strip() or lines[j - 1].strip() in common_bottoms):
            j -= 1
        cleaned.append("\n".join(lines[i:j]))
    return cleaned


def _drop_page_numbers(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not _PAGE_NUMBER_LINE.match(line)
    )


def _dehyphenate(text: str) -> str:
    return _DEHYPHENATE.sub(r"\1\2", text)


def _cut_references(text: str) -> str:
    match = _REFERENCES_HEADING.search(text)
    if not match:
        return text
    return text[: match.start()].rstrip()


def _clean(text: str) -> str:
    text = text.replace("\x00", "")
    text = _dehyphenate(text)
    text = _drop_page_numbers(text)
    text = _cut_references(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract(pdf_bytes: bytes, extractor: Extractor = "pymupdf") -> str:
    
    if not pdf_bytes:
        raise PdfExtractionError("pdf_bytes está vazio")

    if extractor == "docling":
        raw = _extract_markdown_docling(pdf_bytes)
    elif extractor == "pymupdf":
        pages = _strip_repeated_headers_footers(_extract_pages_pymupdf(pdf_bytes))
        raw = "\n\n".join(pages)
    else:
        raise ValueError(f"extractor desconhecido: {extractor!r}")

    return _clean(raw)
