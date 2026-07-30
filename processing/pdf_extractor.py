"""
Extração de texto limpo de PDFs de papers.

Extrator único: `pymupdf` — rápido, texto cru por página, com dedup explícito
de headers/footers olhando o topo e a base de cada página.

Sobre a saída aplicamos as regras de limpeza estrutural:
- Dehifenização de quebras de linha (`word-\nword` -> `wordword`).
- Remoção de linhas que são só números de página.
- Corte da seção de referências bibliográficas até o fim.

O Docling foi avaliado e descartado: dava `std::bad_alloc` em papers maiores
no setup local (CPU/Windows) e entregava saída truncada, além de arrastar
torch/transformers próprios para a imagem. PyMuPDF entrega texto completo em
menos de 1s.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Literal

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

Extractor = Literal["pymupdf"]


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
    # NUL bytes (0x00) aparecem em PDFs com fontes mal mapeadas e quebram
    # campos TEXT do Postgres — remover antes de qualquer outra coisa.
    text = text.replace("\x00", "")
    text = _dehyphenate(text)
    text = _drop_page_numbers(text)
    text = _cut_references(text)
    # Compactar 3+ quebras em uma.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract(pdf_bytes: bytes, extractor: Extractor = "pymupdf") -> str:
    """
    Extrai texto limpo do PDF.

    `extractor="pymupdf"` (único suportado) deduplica headers/footers e devolve
    texto plano; a saída passa pelas regras de limpeza (`_clean`).
    """
    if not pdf_bytes:
        raise PdfExtractionError("pdf_bytes está vazio")

    if extractor != "pymupdf":
        raise ValueError(f"extractor desconhecido: {extractor!r}")

    pages = _strip_repeated_headers_footers(_extract_pages_pymupdf(pdf_bytes))
    return _clean("\n\n".join(pages))
