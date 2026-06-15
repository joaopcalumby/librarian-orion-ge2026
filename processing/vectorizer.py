from __future__ import annotations

import logging
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)


_model = None
_device: Optional[str] = None


def _get_model():
    global _model, _device
    if _model is not None:
        return _model

    # Imports adiados: torch + sentence-transformers são pesados.
    import torch
    from sentence_transformers import SentenceTransformer

    _device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info(
        "Carregando %s em %s (primeira chamada baixa ~2.3GB).",
        settings.BGE_MODEL, _device,
    )
    _model = SentenceTransformer(settings.BGE_MODEL, device=_device)
    return _model


def vectorize_chunks(chunks: list[dict], batch_size: int = 4) -> list[dict]:
    
    if not chunks:
        return []

    work = [c for c in chunks if c.get("chunk_text", "").strip()]
    if not work:
        return []

    model = _get_model()
    texts = [c["chunk_text"] for c in work]

    logger.info("Vetorizando %d chunks em batch_size=%d (device=%s)...",
                len(texts), batch_size, _device)
    vectors = model.encode(
        texts,
        batch_size=batch_size,
        convert_to_numpy=True,
        show_progress_bar=False,
        normalize_embeddings=True,
    )

    if vectors.shape[1] != settings.VECTOR_DIM:
        raise RuntimeError(
            f"Dimensão inesperada: {vectors.shape[1]} (esperado {settings.VECTOR_DIM})"
        )

    return [{**c, "vector": vectors[i].tolist()} for i, c in enumerate(work)]
