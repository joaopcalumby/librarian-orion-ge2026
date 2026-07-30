# Librarian v1.0.5

Sistema RAG sobre literatura acadêmica e técnica. O usuário conversa com um agente que responde a partir dos documentos indexados, sempre citando o paper ou artigo de origem com link.

Esta versão fecha o ciclo do **Grupo de Estudos de Engenharia de Dados do Laboratório Orion** (instrutor: Gean Santos, Maceió/2026), incluindo a separação entre aplicação interna e externa e o agente conversacional previstos no curso.

---

## Arquitetura

O sistema é dividido em duas aplicações que conversam por HTTP.

A **aplicação interna** recebe documentos, processa e indexa. Sobe sozinha e expõe uma API estável.

A **aplicação externa** é o agente conversacional. Não toca no banco: pergunta à interna por HTTP.

```
                    ┌──────────────────────────────┐
   usuário  ───────►│  agente (Agno + Gemini)      │  :8008
                    │  memória de sessão: Postgres │
                    └──────────────┬───────────────┘
                                   │ tool buscar_documentos
                                   │ POST /search
                    ┌──────────────▼───────────────┐
                    │  API interna (FastAPI)       │  :8000
                    │  /pdf  /site  /search        │
                    └───────┬──────────────┬───────┘
                            │              │
              ┌─────────────▼───┐   ┌──────▼──────────┐
              │ PostgreSQL      │   │ Qdrant          │
              │ papers          │   │ vetores 1024d   │
              │ chunks          │◄──┤ point id =      │
              │ chunks_with_meta│   │   chunk_id      │
              └─────────────────┘   └─────────────────┘
```

O `chunk_id` é um UUID gerado no chunker e usado como chave primária no Postgres e como id do ponto no Qdrant. É o que permite recuperar um vetor e voltar ao texto e aos metadados de citação.

### Pipeline de ingestão

```
documento
  └─► extração de texto (PyMuPDF para PDF, trafilatura para HTML)
      └─► limpeza (dehifenização, números de página, corte em referências)
          └─► chunking (1200 chars, overlap 200, corte em fronteira de sentença)
              └─► vetorização (BAAI/bge-m3, 1024 dim, L2-normalizado)
                  ├─► PostgreSQL: texto e metadados
                  └─► Qdrant: vetores
```

### Busca

`POST /search` vetoriza a consulta com o mesmo modelo da ingestão, consulta o Qdrant por similaridade de cosseno e hidrata os resultados na view `chunks_with_meta`, devolvendo o texto do trecho junto do título, autores e URL do documento. A citação ancorada sai desse join.

---

## Endpoints

| Método | Path | Body | Sucesso | Erros |
|---|---|---|---|---|
| `GET`  | `/health`  | — | `200 {"status":"ok"}` | — |
| `GET`  | `/sources` | — | `200 {"allowed_hosts":[...]}` | — |
| `POST` | `/pdf`     | `multipart/form-data`, campo `file` | `200 IngestResponse` | `400` (vazio ou não é PDF), `422` (extração falhou) |
| `POST` | `/site`    | `{"url": "..."}` | `200 IngestResponse` | `400` (URL vazia), `404` (domínio fora da whitelist), `422` (conteúdo vazio), `502` (falha de fetch) |
| `POST` | `/search`  | `{"query": "...", "limit": 5}` | `200 SearchResponse` | `400` (query vazia), `422` (limit fora de 1..20) |

`IngestResponse` = `{document_id, source_type, title, chunks, vectors, session_id}`

`SearchResponse` = `{query, results[]}`, cada resultado com `{chunk_id, score, document_id, title, authors, url, chunk_index, text}`

O `document_id` é sintético quando o documento não vem do arXiv:

- PDF enviado: `pdf:<sha256[:12]>`
- Site: `site:<host>:<sha256[:12]>`

Sites são restritos a uma whitelist de domínios cujo conteúdo principal vem no HTML (Medium, Towards Data Science, HuggingFace). Fora dela, `404`.

---

## Como rodar

Requer Docker e Docker Compose.

```bash
cp .env.example .env
docker compose up -d
```

Sobem cinco serviços: `api` (8000), `agno` (8008), `postgres` (5432), `qdrant` (6333) e `minio` (9000/9001).

O agente precisa de um database próprio no Postgres, criado uma única vez:

```bash
docker exec librarian_postgres psql -U librarian -c "CREATE DATABASE agno"
```

Depois preencha `GEMINI_API_KEY` no `.env` e reinicie o serviço:

```bash
docker compose up -d agno
```

### Verificando

```bash
curl http://localhost:8000/health
```

Indexando um PDF:

```bash
curl -F "file=@paper.pdf" http://localhost:8000/pdf
```

Buscando:

```bash
curl -X POST http://localhost:8000/search -H "Content-Type: application/json" -d '{"query":"graph neural networks","limit":3}'
```

### Pipeline batch do arXiv

Busca N papers sobre um tema, grava os PDFs no Bronze (MinIO) e processa tudo até o Ouro:

```bash
docker compose run --rm --entrypoint python api pipeline.py --query "graph neural networks" --n 5
```

---

## Configuração

Tudo vem de variáveis de ambiente, com defaults em `config/settings.py`. As mais relevantes:

| Variável | Default | Para que serve |
|---|---|---|
| `CHUNK_SIZE` | `1200` | Tamanho do chunk em caracteres |
| `CHUNK_OVERLAP` | `200` | Sobreposição entre chunks consecutivos |
| `BGE_MODEL` | `BAAI/bge-m3` | Modelo de embedding |
| `VECTOR_DIM` | `1024` | Dimensão do vetor, precisa bater com o modelo |
| `QDRANT_COLLECTION` | `librarian_chunks` | Nome da collection |
| `ARXIV_DEFAULT_N` | `5` | Papers por execução do pipeline batch |
| `ARXIV_REQUEST_DELAY_SECONDS` | `3.0` | Intervalo entre downloads, exigido pelo arXiv |
| `GEMINI_API_KEY` | — | Chave do modelo do agente |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | Modelo de linguagem do agente |

---

## Stack

| Camada | Ferramenta |
|---|---|
| Servidor HTTP | FastAPI + uvicorn |
| Agente | Agno (AgentOS) + Gemini |
| Cliente HTTP | httpx |
| Extração de PDF | PyMuPDF |
| Extração de HTML | trafilatura |
| Chunking | implementação própria, janela deslizante com fronteira de sentença |
| Embeddings | `BAAI/bge-m3` via sentence-transformers, em CPU |
| Ouro texto | PostgreSQL 16 |
| Ouro vetor | Qdrant |
| Bronze | MinIO |
| Empacotamento | Docker + Docker Compose |

---

## Estrutura

```
librarian-orion-ge2026/
├── docker-compose.yml          # api + agno + postgres + qdrant + minio
├── Dockerfile                  # imagem da aplicação interna
├── requirements.txt
├── pipeline.py                 # CLI batch: arXiv -> Bronze -> Ouro
├── api/
│   ├── server.py               # /health /sources /pdf /site /search
│   └── sources.py              # whitelist de domínios
├── agno/
│   ├── app.py                  # agente e tool de busca
│   ├── Dockerfile
│   └── pyproject.toml
├── config/settings.py
├── scraper/
│   ├── arxiv_client.py
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

## Limitações conhecidas

**A busca em CPU leva cerca de 10 segundos.** A vetorização da consulta domina o tempo de resposta. Habilitar GPU no container exige o NVIDIA Container Toolkit no host.

**A busca é apenas semântica.** O retrieval híbrido com BM25S está planejado, não implementado.

**O Medium bloqueia por anti-bot em parte das requisições.** O fingerprint TLS do httpx difere do de um browser real. Contornar exigiria browser headless ou proxy residencial.

**O Bronze só é usado pelo pipeline batch.** O endpoint `/pdf` processa o arquivo em memória e não guarda o original.

**Podem existir chunks órfãos.** Execuções antigas deixaram registros no Postgres sem o vetor correspondente no Qdrant. A busca ignora o que não conseguir hidratar.

---

## Branches

| Branch | Conteúdo |
|---|---|
| `main` | Versão publicada |
| `dev` | Desenvolvimento |
| `ge` | Entrega congelada do Grupo de Estudos: apenas a aplicação interna, sem busca nem agente |
