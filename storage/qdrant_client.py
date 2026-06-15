from __future__ import annotations

import logging
from typing import Iterable

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from config import settings

logger = logging.getLogger(__name__)


def connect() -> QdrantClient:
    return QdrantClient(host=settings.QDRANT_HOST, port=settings.QDRANT_PORT)


def ensure_collection(
    client: QdrantClient,
    name: str = settings.QDRANT_COLLECTION,
    dim: int = settings.VECTOR_DIM,
) -> None:
    """Cria a collection se ainda não existir. Idempotente."""
    existing = {c.name for c in client.get_collections().collections}
    if name in existing:
        logger.debug("[Qdrant] collection '%s' já existe.", name)
        return

    client.create_collection(
        collection_name=name,
        vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
    )
    logger.info("[Qdrant] collection '%s' criada (dim=%d, cosine).", name, dim)


def upsert_vectors(
    client: QdrantClient,
    chunks_with_vectors: Iterable[dict],
    name: str = settings.QDRANT_COLLECTION,
) -> int:

    items = list(chunks_with_vectors)
    if not items:
        return 0

    points = [
        qm.PointStruct(
            id=item["chunk_id"],
            vector=item["vector"],
            payload={"arxiv_id": item["arxiv_id"]},
        )
        for item in items
    ]
    client.upsert(collection_name=name, points=points, wait=True)
    logger.info("[Qdrant] upsert de %d pontos em '%s'.", len(points), name)
    return len(points)


def search(
    client: QdrantClient,
    query_vector: list[float],
    limit: int = 5,
    name: str = settings.QDRANT_COLLECTION,
) -> list[dict]:
    result = client.query_points(
        collection_name=name,
        query=query_vector,
        limit=limit,
        with_payload=True,
    )
    return [{"chunk_id": str(h.id), "score": h.score, "payload": h.payload} for h in result.points]
