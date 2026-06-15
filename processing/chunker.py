from __future__ import annotations

import logging
import re
import uuid

from config import settings

logger = logging.getLogger(__name__)


_BOUNDARY_LOOKBACK_RATIO = 0.10

_BOUNDARY_RE = re.compile(r"[.!?]\s|\n")


def _find_clean_cut(text: str, start: int, hard_end: int) -> int:

    window = hard_end - start
    lookback = max(1, int(window * _BOUNDARY_LOOKBACK_RATIO))
    soft_start = max(start, hard_end - lookback)

    last_match_end = -1
    for m in _BOUNDARY_RE.finditer(text, soft_start, hard_end):
        last_match_end = m.end()
    return last_match_end if last_match_end > 0 else hard_end


def chunk_text(
    text: str,
    paper_meta: dict,
    chunk_size: int = settings.CHUNK_SIZE,
    chunk_overlap: int = settings.CHUNK_OVERLAP,
) -> list[dict]:

    if chunk_size <= 0:
        raise ValueError("chunk_size deve ser > 0")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap deve estar em [0, chunk_size)")
    for required in ("arxiv_id", "title", "authors", "arxiv_url"):
        if required not in paper_meta:
            raise KeyError(f"paper_meta sem campo obrigatório: {required!r}")

    cleaned = text.strip() if text else ""
    if not cleaned:
        return []

    if len(cleaned) <= chunk_size:
        return [_build_chunk(cleaned, 0, paper_meta)]

    step = chunk_size - chunk_overlap
    chunks: list[dict] = []
    start = 0
    index = 0
    n = len(cleaned)

    while start < n:
        hard_end = min(start + chunk_size, n)
        end = _find_clean_cut(cleaned, start, hard_end) if hard_end < n else hard_end
        piece = cleaned[start:end].strip()
        if piece:
            chunks.append(_build_chunk(piece, index, paper_meta))
            index += 1
        if end >= n:
            break
        next_start = end - chunk_overlap
        if next_start <= start:
            next_start = start + step
        start = next_start

    return chunks


def _build_chunk(piece: str, index: int, paper_meta: dict) -> dict:
    return {
        "chunk_id": str(uuid.uuid4()),
        "arxiv_id": paper_meta["arxiv_id"],
        "title": paper_meta["title"],
        "authors": paper_meta["authors"],
        "arxiv_url": paper_meta["arxiv_url"],
        "chunk_index": index,
        "chunk_text": piece,
    }
