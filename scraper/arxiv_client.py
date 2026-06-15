from __future__ import annotations

import logging
from typing import Optional

import arxiv

from config import settings
from scraper.deduplicator import dedupe_by_arxiv_id

logger = logging.getLogger(__name__)


# Cliente único reutilizado entre chamadas: o `delay_seconds` da lib garante
# que requisições consecutivas respeitam o rate limit recomendado pelo arXiv
# (>=3s), mesmo quando duas chamadas a `search()` acontecem em sequência.
_client: Optional[arxiv.Client] = None


def _get_client() -> arxiv.Client:
    global _client
    if _client is None:
        _client = arxiv.Client(
            page_size=100,
            delay_seconds=settings.ARXIV_REQUEST_DELAY_SECONDS,
            num_retries=3,
        )
    return _client


def _normalize(result: arxiv.Result) -> dict:
    short_id = result.get_short_id()
    arxiv_id = short_id.split("v")[0]

    return {
        "arxiv_id": arxiv_id,
        "title": result.title.strip(),
        "authors": [a.name for a in result.authors],
        "abstract": result.summary.strip(),
        "published": result.published.isoformat() if result.published else None,
        "pdf_url": result.pdf_url,
        "arxiv_url": result.entry_id,
    }


def search(query: str, n: int = settings.ARXIV_DEFAULT_N) -> list[dict]:
    if not query or not query.strip():
        raise ValueError("query não pode ser vazia")
    if n <= 0:
        raise ValueError("n deve ser > 0")

    client = _get_client()
    search_obj = arxiv.Search(
        query=query,
        max_results=n,
        sort_by=arxiv.SortCriterion.Relevance,
    )

    logger.info("arxiv.search query=%r n=%d", query, n)
    results = list(client.results(search_obj))
    logger.info("arxiv.search returned %d results", len(results))

    papers = [_normalize(r) for r in results]
    return dedupe_by_arxiv_id(papers)
