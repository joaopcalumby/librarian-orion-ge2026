from typing import Iterable


def dedupe_by_arxiv_id(papers: Iterable[dict]) -> list[dict]:
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
