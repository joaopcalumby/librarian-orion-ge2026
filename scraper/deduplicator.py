"""
Deduplicação de papers por `arxiv_id`, identificador único garantido pela API
do arXiv.

O escopo é uma execução: a deduplicação entre execuções faz parte da busca
incremental, ainda não implementada.
"""

from typing import Iterable


def dedupe_by_arxiv_id(papers: Iterable[dict]) -> list[dict]:
    """
    Remove papers com `arxiv_id` repetido dentro da lista, mantendo a
    primeira ocorrência. Papers sem `arxiv_id` são descartados — o pipeline
    só processa o que veio do arXiv.
    """
    seen: set[str] = set()
    unique: list[dict] = []

    for paper in papers:
        arxiv_id = (paper.get("arxiv_id") or "").strip()
        if not arxiv_id:
            continue
        if arxiv_id in seen:
            continue
        seen.add(arxiv_id)
        unique.append(paper)

    return unique
