# Librarian — Grupo de Estudos (branch `ge`)

API HTTP interna do Librarian, focada no que cabe à entrega do **Grupo de Estudos de Engenharia de Dados do Laboratório Orion** (instrutor: Gean Santos, Maceió/2026).

Esta branch contém **apenas o servidor interno** (ingestão + processamento + Ouro). A integração com LLM e interface gráfica fica no projeto principal nas branches `main` e `dev`.

---

## O que faz

Recebe **um documento por vez** via HTTP, processa o conteúdo e indexa em uma arquitetura medallion:

- **Bronze** — MinIO (PDFs brutos + metadados JSON, por sessão)
- **Transformação** — extração de texto, limpeza, chunking (1200/200, configurável), vetorização BGE-M3
- **Ouro** — PostgreSQL (texto + metadados) e Qdrant (vetores 1024-dim), com **mesmo `chunk_id` ligando as duas pontas**

Suporta duas fontes via endpoints distintos:

- **PDF** — upload direto do arquivo.
- **Sites mapeados** — apenas domínios em whitelist cujo conteúdo principal é entregue no HTML (Medium, Towards Data Science, HuggingFace blog). Outros domínios retornam **404**.

---

## Endpoints

| Método | Path | Body | Sucesso | Erros |
|---|---|---|---|---|
| `GET`  | `/health`  | — | `200 {"status":"ok"}` | — |
| `GET`  | `/sources` | — | `200 {"allowed_hosts":[...]}` | — |
| `POST` | `/pdf`     | `multipart/form-data` com campo `file` (PDF) | `200 IngestResponse` | `400` (vazio / não é PDF), `422` (extração falhou) |
| `POST` | `/site`    | `application/json` `{"url": "..."}` | `200 IngestResponse` | `400` (URL vazia), `404` (domínio fora da whitelist), `422` (conteúdo vazio), `502` (falha de fetch) |

**`IngestResponse`** = `{document_id, source_type, title, chunks, vectors, session_id}`.

`document_id` é sintético, gerado dentro do servidor:
- PDF: `pdf:<sha256[:12]>`
- Site: `site:<host>:<sha256[:12]>`

---

## Fluxo interno

```
cliente externo
    │  POST /pdf  ou  POST /site
    ▼
FastAPI (uvicorn, async; CPU-bound em threadpool)
    │
    ├── /pdf:  upload → magic bytes (%PDF) → extract (PyMuPDF)
    └── /site: whitelist → fetch HTML (UA de browser) → trafilatura
    │
    ▼
chunking (1200 chars / overlap 200, fronteira de sentença, UUID por chunk)
    │
    ▼
vetorização (sentence-transformers + BAAI/bge-m3, 1024-dim, L2-normalizado)
    │
    ├──► PostgreSQL: papers + chunks + view chunks_with_meta
    └──► Qdrant: collection com mesmo chunk_id como point id
```

Em paralelo, `/pdf` também faz upload do PDF original ao Bronze (MinIO) só quando vier do pipeline batch da `pipeline.py`. A API atual processa o PDF em memória — o Bronze fica reservado para o caminho batch arXiv.

---

## Stack

| Camada | Ferramenta |
|---|---|
| Servidor HTTP | FastAPI + uvicorn |
| Cliente HTTP (fetch de sites) | httpx (async) |
| Extração de site | trafilatura |
| Extração de PDF | PyMuPDF |
| Chunking | custom (janela deslizante com fronteira de sentença) |
| Embeddings | `BAAI/bge-m3` via sentence-transformers (CPU) |
| Ouro texto | PostgreSQL 16 |
| Ouro vetor | Qdrant |
| Bronze (pipeline batch) | MinIO |
| Empacotamento | Docker + docker-compose (4 serviços) |

---

## Estrutura do repositório

```
librarian-orion-ge2026/
├── Dockerfile
├── docker-compose.yml          # api + postgres + qdrant + minio
├── requirements.txt
├── .env.example
├── pipeline.py                 # CLI batch (arXiv → Ouro), opcional
├── api/
│   ├── server.py               # FastAPI com /pdf, /site, /health, /sources
│   └── sources.py              # whitelist de domínios aceitos
├── config/
│   └── settings.py
├── scraper/
│   ├── arxiv_client.py         # busca no arXiv (usado pelo pipeline batch)
│   ├── pdf_downloader.py
│   └── deduplicator.py
├── processing/
│   ├── pdf_extractor.py
│   ├── chunker.py
│   └── vectorizer.py
└── storage/
    ├── minio_client.py
    ├── postgres_client.py
    └── qdrant_client.py
```

---

## Instalação e uso

### Pré-requisitos

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (ou Docker Engine + docker-compose v2).
- Conexão de internet para a primeira execução: o modelo BGE-M3 (~2.3GB) é baixado uma vez e fica em um volume Docker (`hf_cache`).

### Setup

```bash
git clone -b ge https://github.com/joaopcalumby/librarian-orion-ge2026.git
cd librarian-orion-ge2026

cp .env.example .env

docker compose up -d --build
```

O serviço `api` fica disponível em **<http://localhost:8000>**. Documentação interativa do FastAPI em **<http://localhost:8000/docs>**.

Crie o bucket Bronze uma vez (apenas se for usar o `pipeline.py` batch):

- <http://localhost:9001> · login `minioadmin/minioadmin` · criar bucket `librarian-bronze`.

### Exemplos de uso

```bash
# Health
curl http://localhost:8000/health

# Listar domínios aceitos por /site
curl http://localhost:8000/sources

# Ingerir um PDF local
curl -X POST http://localhost:8000/pdf -F "file=@paper.pdf"

# Ingerir um artigo do Medium
curl -X POST http://localhost:8000/site \
  -H "Content-Type: application/json" \
  -d '{"url":"https://medium.com/@ageitgey/machine-learning-is-fun-80ea3ec3c471"}'

# Domínio fora da whitelist → 404
curl -X POST http://localhost:8000/site \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/qualquer-coisa"}'
```

### Inspecionando o Ouro

```bash
# Texto: lista de documentos indexados + contagem de chunks
docker exec -it librarian_postgres psql -U librarian -d librarian \
  -c "SELECT arxiv_id, title FROM papers ORDER BY first_seen_at DESC LIMIT 10" \
  -c "SELECT arxiv_id, count(*) FROM chunks GROUP BY arxiv_id"

# Vetores: total + busca pelo id de um chunk específico
curl http://localhost:6333/collections/librarian_chunks
curl http://localhost:6333/collections/librarian_chunks/points/<chunk_id>
```

### Adicionando um novo domínio à whitelist

Edite `api/sources.py` e inclua o host (sem `www.`) em `ALLOWED_HOSTS`. Reconstrua a imagem do `api`:

```bash
docker compose up -d --build api
```

### CLI batch (arXiv → Ouro, opcional)

A CLI original ainda existe e roda o caminho completo via arXiv (busca por query, download para Bronze, etc.):

```bash
docker compose run --rm --entrypoint python api pipeline.py --query "graph neural networks" --n 3
```

---

## Sobre as branches

| Branch | Propósito |
|---|---|
| `main`, `dev` | Projeto principal (com LLM + interface gráfica nas próximas entregas) |
| `ge`         | Entrega para o Grupo de Estudos: apenas o servidor interno desta documentação |

A branch `ge` é o que foi pedido pelo professor do Grupo de Estudos: aplicação interna com endpoints `/pdf` e `/site` rodando em container Docker junto com os bancos.
