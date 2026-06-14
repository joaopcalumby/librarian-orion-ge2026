"""
Extração de texto limpo de PDFs de papers.

Dois extratores intercambiáveis:
- `docling` (default): bom em layouts complexos (duas colunas, tabelas),
  já entrega markdown estruturado que descarta muito ruído.
- `pymupdf`: rápido, texto cru por página — usamos para deduplicar
  headers/footers que se repetem entre páginas.

Em ambos os caminhos aplicamos as mesmas regras de limpeza estrutural:
- Dehifenização de quebras de linha (`word-\nword` -> `wordword`).
- Remoção de linhas que são só números de página.
- Corte da seção de referências bibliográficas até o fim.

Para Docling, headers/footers já são em geral removidos pelo próprio export
para markdown; para PyMuPDF aplicamos dedup explícito olhando o topo e a
base de cada página.
"""

from __future__ import annotations

import io
import logging
import re
from collections import Counter
from typing import Literal

import fitz  # PyMuPDF
from docling.datamodel.base_models import DocumentStream
from docling.document_converter import DocumentConverter

logger = logging.getLogger(__name__)

Extractor = Literal["docling", "pymupdf"]


class PdfExtractionError(Exception):
    """Erro durante extração; o orquestrador captura e marca o paper como falho."""


# Cabeçalhos que iniciam a seção de referências; truncamos a partir daí.
_REFERENCES_HEADING = re.compile(
    r"^\s{0,3}#{0,6}\s*(References|Referências|Bibliography|Works Cited)\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Linha cujo conteúdo é apenas número de página (eventualmente com "Page N").
_PAGE_NUMBER_LINE = re.compile(r"^\s*(?:page\s+)?\d{1,4}\s*$", re.IGNORECASE)

# Hifenização no fim de linha: "retrie-\nval" -> "retrieval".
_DEHYPHENATE = re.compile(r"(\w)-\n(\w)")


# Cliente Docling reutilizado entre chamadas: o construtor é caro (baixa
# modelos na primeira execução).
_docling_converter: DocumentConverter | None = None


def _get_docling() -> DocumentConverter:
    global _docling_converter
    if _docling_converter is None:
        logger.info("Inicializando DocumentConverter (primeira execução baixa modelos).")
        _docling_converter = DocumentConverter()
    return _docling_converter


def _extract_pages_pymupdf(pdf_bytes: bytes) -> list[str]:
    """Lista com o texto cru de cada página."""
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
    """Markdown estruturado do documento inteiro (já com headers/footers descartados)."""
    converter = _get_docling()
    stream = DocumentStream(name="paper.pdf", stream=io.BytesIO(pdf_bytes))
    try:
        result = converter.convert(stream)
    except Exception as e:
        raise PdfExtractionError(f"Docling falhou ao converter o PDF: {e}") from e
    return result.document.export_to_markdown()


def _strip_repeated_headers_footers(pages: list[str], threshold: float = 0.5) -> list[str]:
    """
    Remove primeiras/últimas linhas que se repetem em >=threshold das páginas.
    Cobre cabeçalho institucional e rodapé do tipo "Preprint - Lab X".
    """
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
    text = _dehyphenate(text)
    text = _drop_page_numbers(text)
    text = _cut_references(text)
    # Compactar 3+ quebras em uma.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract(pdf_bytes: bytes, extractor: Extractor = "pymupdf") -> str:
    """
    Extrai texto limpo do PDF.

    `extractor="pymupdf"` (default) deduplica headers/footers e devolve texto
    plano — escolhido como default depois que Docling apresentou `std::bad_alloc`
    em papers maiores no setup local (CPU/Windows), entregando saída truncada.
    `extractor="docling"` continua disponível para papers curtos / setup com
    GPU em que o markdown estruturado vale a troca. Em ambos os casos a saída
    passa pelas mesmas regras de limpeza (`_clean`).
    """
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
