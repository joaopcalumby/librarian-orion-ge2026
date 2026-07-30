"""
Pipeline end-to-end do Librarian.

Fluxo: tema do usuário -> arXiv -> download de PDF -> MinIO (Bronze) ->
extração de texto -> chunking -> vetorização BGE-M3 -> PostgreSQL (texto)
+ Qdrant (vetores), com o mesmo `chunk_id` ligando as duas pontas.

Resiliência: cada paper é processado isoladamente; uma falha em download,
extração ou vetorização marca o paper como falho no log final mas não trava
os demais.

Uso:
    python pipeline.py --query "graph neural networks" --n 5
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from config import settings
from processing.chunker import chunk_text
from processing.pdf_extractor import PdfExtractionError, extract
from processing.vectorizer import vectorize_chunks
from scraper.arxiv_client import search
from scraper.pdf_downloader import download_papers
from storage import postgres_client
from storage.minio_client import (
    ensure_bucket,
    get_minio_client,
)
from storage.qdrant_client import connect as qdrant_connect
from storage.qdrant_client import ensure_collection, upsert_vectors

logger = logging.getLogger("pipeline")


@dataclass
class PaperOutcome:
    arxiv_id: str
    success: bool
    chunks: int = 0
    error: Optional[str] = None
    stage: Optional[str] = None


@dataclass
class RunSummary:
    session_id: str
    query: str
    requested: int
    outcomes: list[PaperOutcome] = field(default_factory=list)

    @property
    def successes(self) -> int:
        return sum(1 for o in self.outcomes if o.success)

    @property
    def failures(self) -> list[PaperOutcome]:
        return [o for o in self.outcomes if not o.success]


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if not verbose else logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _process_paper(
    paper: dict,
    session_id: str,
    qdrant_client,
) -> PaperOutcome:
    """Encadeia extração -> chunking -> vetorização -> Postgres+Qdrant para um paper."""
    arxiv_id = paper["arxiv_id"]
    pdf_bytes = paper["_pdf_bytes"]

    try:
        logger.info("[%s] extraindo texto...", arxiv_id)
        text = extract(pdf_bytes)
    except PdfExtractionError as e:
        return PaperOutcome(arxiv_id=arxiv_id, success=False, stage="extract", error=str(e))

    if not text.strip():
        return PaperOutcome(arxiv_id=arxiv_id, success=False, stage="extract", error="texto vazio")

    try:
        logger.info("[%s] chunking (%d chars)...", arxiv_id, len(text))
        chunks = chunk_text(text, paper)
    except (ValueError, KeyError) as e:
        return PaperOutcome(arxiv_id=arxiv_id, success=False, stage="chunk", error=str(e))

    if not chunks:
        return PaperOutcome(arxiv_id=arxiv_id, success=False, stage="chunk", error="0 chunks")

    try:
        logger.info("[%s] vetorizando %d chunks...", arxiv_id, len(chunks))
        chunks_with_vecs = vectorize_chunks(chunks)
    except Exception as e:  # noqa: BLE001 — vetorização pode estourar de mil formas
        return PaperOutcome(arxiv_id=arxiv_id, success=False, stage="vectorize", error=str(e))

    try:
        logger.info("[%s] persistindo no Postgres + Qdrant...", arxiv_id)
        # Sessão Postgres por paper: falha em um paper não derruba os outros.
        with postgres_client.session() as pg_conn:
            postgres_client.upsert_paper(pg_conn, paper)
            postgres_client.insert_chunks(pg_conn, chunks_with_vecs, session_id=session_id)
        upsert_vectors(qdrant_client, chunks_with_vecs)
    except Exception as e:  # noqa: BLE001 — Postgres/Qdrant podem estourar diversos erros
        return PaperOutcome(arxiv_id=arxiv_id, success=False, stage="persist", error=str(e))

    return PaperOutcome(arxiv_id=arxiv_id, success=True, chunks=len(chunks))


def run(query: str, n: int = settings.ARXIV_DEFAULT_N) -> RunSummary:
    session_id = f"sess-{uuid.uuid4().hex[:8]}"
    summary = RunSummary(session_id=session_id, query=query, requested=n)

    logger.info("=" * 60)
    logger.info("session_id=%s  query=%r  n=%d", session_id, query, n)
    logger.info("=" * 60)

    papers = search(query, n=n)
    if not papers:
        logger.warning("Nenhum paper retornado para %r.", query)
        return summary
    logger.info("arXiv retornou %d papers.", len(papers))

    minio = get_minio_client(
        settings.MINIO_ENDPOINT, settings.MINIO_ACCESS_KEY,
        settings.MINIO_SECRET_KEY, settings.MINIO_SECURE,
    )
    ensure_bucket(minio, settings.MINIO_BUCKET_BRONZE)

    download_results = download_papers(
        minio, settings.MINIO_BUCKET_BRONZE, session_id, papers
    )

    by_id = {p["arxiv_id"]: p for p in papers}
    pdfs_for_pipeline: list[dict] = []
    for r in download_results:
        if not r.success:
            summary.outcomes.append(
                PaperOutcome(arxiv_id=r.arxiv_id, success=False, stage="download", error=r.error)
            )
            continue
        # Relê do MinIO em vez de reaproveitar os bytes do download: é o que
        # prova que o Bronze foi realmente gravado e é legível.
        response = minio.get_object(settings.MINIO_BUCKET_BRONZE, r.pdf_object)
        try:
            pdf_bytes = response.read()
        finally:
            response.close()
            response.release_conn()
        paper = by_id[r.arxiv_id]
        paper["_pdf_bytes"] = pdf_bytes
        pdfs_for_pipeline.append(paper)

    logger.info("Bronze: %d/%d papers persistidos.", len(pdfs_for_pipeline), len(papers))

    if not pdfs_for_pipeline:
        return summary

    # Schemas e coleções uma vez só; cada paper abre a própria transação
    # depois, para que uma falha não derrube os demais.
    qdrant = qdrant_connect()
    ensure_collection(qdrant)
    with postgres_client.session() as pg_conn:
        postgres_client.init_schema(pg_conn)

    for paper in pdfs_for_pipeline:
        outcome = _process_paper(paper, session_id, qdrant)
        summary.outcomes.append(outcome)
        if outcome.success:
            logger.info("[%s] ✓ %d chunks persistidos.", outcome.arxiv_id, outcome.chunks)
        else:
            logger.error("[%s] ✗ stage=%s err=%s",
                         outcome.arxiv_id, outcome.stage, outcome.error)

    return summary


def _print_summary(summary: RunSummary) -> None:
    print()
    print("=" * 60)
    print(f"RESUMO da sessão {summary.session_id}")
    print(f"  query: {summary.query!r}")
    print(f"  papers requisitados: {summary.requested}")
    print(f"  papers processados:  {len(summary.outcomes)}")
    print(f"  sucessos: {summary.successes}")
    print(f"  falhas:   {len(summary.failures)}")
    total_chunks = sum(o.chunks for o in summary.outcomes if o.success)
    print(f"  total de chunks indexados: {total_chunks}")
    if summary.failures:
        print("  papers com falha:")
        for o in summary.failures:
            print(f"    - {o.arxiv_id}  stage={o.stage}  err={o.error}")
    print("=" * 60)


def main() -> int:
    parser = argparse.ArgumentParser(description="Librarian pipeline (arXiv -> Bronze -> Ouro)")
    parser.add_argument("--query", "-q", required=True, help="tema da busca no arXiv")
    parser.add_argument("--n", type=int, default=settings.ARXIV_DEFAULT_N, help="número de papers")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    _setup_logging(args.verbose)
    t0 = time.time()
    summary = run(args.query, n=args.n)
    logger.info("Tempo total: %.1fs", time.time() - t0)
    _print_summary(summary)

    return 0 if summary.successes > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
