"""
Cliente da API do arXiv.

Usa a biblioteca `arxiv` (wrapper sobre o endpoint Atom oficial), que já cobre
rate limiting e paginação. Documentação: https://info.arxiv.org/help/api/

A função `search()` é o ponto de entrada usado pelo pipeline: recebe a query
do usuário e devolve até N papers normalizados em dicts, prontos para o
deduplicador e o downloader.
"""

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
    """Converte um `arxiv.Result` no dict que o resto do pipeline consome."""
    # `entry_id` vem como "http://arxiv.org/abs/2401.12345v2"; queremos
    # o id curto sem versão para deduplicar e nomear o PDF.
    short_id = result.get_short_id()  # ex.: "2401.12345v2"
    arxiv_id = short_id.split("v")[0]  # ex.: "2401.12345"

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
    """
    Busca até `n` papers no arXiv que correspondam a `query`.

    Retorna lista de dicts normalizados, já deduplicados por `arxiv_id`.
    Lista vazia se a API não retornar nada.
    """
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
