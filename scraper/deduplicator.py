"""
Deduplicação de papers por `arxiv_id`.

A Primeira Entrega deduplicava por DOI + ano, com persistência em disco
(`logs/seen_dois.json`). Para a Segunda Entrega o `arxiv_id` é identificador
único garantido pela própria API do arXiv — não há necessidade de filtro por
ano, e a deduplicação por execução é suficiente. Persistência cross-execução
fica para a fase futura de "busca incremental".
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
