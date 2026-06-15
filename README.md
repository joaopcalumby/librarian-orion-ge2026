# Librarian

Sistema RAG (*Retrieval-Augmented Generation*) acadêmico de propósito geral. O usuário define um tema, o sistema busca papers no [arXiv](https://arxiv.org/), baixa os PDFs e processa o conteúdo completo até deixá-lo indexado como texto (PostgreSQL) e como vetores (Qdrant), prontos para serem consultados por um LLM nas próximas entregas.

Projeto desenvolvido no **Grupo de Estudos de Engenharia de Dados do Laboratório Orion** (instrutor: Gean Santos, Maceió/2026).

---

## Contexto e finalidade

A entrega anterior do curso cobria apenas a **Geração** + **Bronze** sobre metadados/abstracts vindos de OpenAlex e Semantic Scholar. Esta entrega exige processar o **paper inteiro** (não só o abstract) até o **Ouro**: chunks textuais no Postgres, vetores no Qdrant, e localização do texto a partir do ID do vetor (vínculo bidirecional).

Em paralelo, a fonte foi redirecionada para o **arXiv** — open access por padrão, API gratuita, sem paywall — e o sistema deixou de ser específico de aquicultura para virar uma ferramenta genérica parametrizada pelo tema do usuário.

---

## O que o sistema faz

1. Recebe uma **query em texto livre** e um número de papers desejado (default: 5).
2. **Busca no arXiv** os papers mais relevantes para a query.
3. **Baixa os PDFs** com retry e validação de integridade (magic bytes `%PDF`).
4. Armazena PDFs e metadados no **MinIO** (camada Bronze), organizados por sessão.
5. **Extrai o texto limpo** de cada PDF.
6. **Divide o texto em chunks** de tamanho controlado, com sobreposição, atribuindo um `chunk_id` único por chunk (UUIDv4).
7. **Vetoriza cada chunk** com o modelo BGE-M3, produzindo embeddings densos de 1024 dimensões.
8. **Persiste no Ouro**:
   - texto dos chunks + metadados do paper no **PostgreSQL**;
   - vetores no **Qdrant**, usando o mesmo `chunk_id` como identificador do ponto.

O resultado é uma base consultável: a partir do ID de um vetor, é possível recuperar o texto original do chunk e os metadados do paper de origem (título, autores, URL no arXiv) numa única query.

---

## Como faz — fluxo da operação

```
Usuário ──► pipeline.py
              │
              ▼
        arXiv API ──► metadados + pdf_url
              │
              ▼
        MinIO (BRONZE) ──► sessions/{sid}/pdfs/{arxiv_id}.pdf
              │                sessions/{sid}/metadata/{arxiv_id}.json
              ▼
        Extração de texto (PyMuPDF)
        + limpeza (NUL bytes, hifenização, headers/footers, referências)
              │
              ▼
        Chunking (1200 chars, overlap 200, fronteira de sentença, UUID por chunk)
              │
              ▼
        Vetorização (BGE-M3 dense, 1024 dim)
              │
        ┌─────┴─────┐
        ▼           ▼
   PostgreSQL    Qdrant
   (chunks +     (vetores,
    metadados)    mesmo chunk_id)
```

Resiliência: cada paper é processado em isolamento. Falha em download, extração ou vetorização registra o paper como falho no log final, sem interromper o processamento dos demais.

### Pré-processamento dos dados — etapas

| # | Etapa | Por quê |
|---|---|---|
| 1 | Download com retry exponencial (3 tentativas) | Rede acadêmica falha; transitórios resolvem com retentativa |
| 2 | Validação de magic bytes (`%PDF`) | `Content-Type` é mentiroso quando há proxy; magic bytes são honestos |
| 3 | Extração com PyMuPDF | Rápido, robusto, preserva ordem de leitura em layouts de duas colunas |
| 4 | Strip de bytes `\x00` | PDFs com fontes mal mapeadas geram NUL bytes que Postgres `TEXT` rejeita |
| 5 | Dehifenização (`pala-\nvra` → `palavra`) | Quebra de linha não pode partir termos técnicos antes da vetorização |
| 6 | Remoção de números de página + headers/footers repetidos | Ruído estrutural sem valor semântico |
| 7 | Corte da seção de referências | Lista de citações sem coerência de discurso degrada o índice |
| 8 | Chunking com fronteira de sentença | Tamanho fixo (requisito) sem partir frases no meio |
| 9 | Embedding L2-normalizado (BGE-M3 dense, 1024 dim) | Permite cosine similarity simples e estável no Qdrant |

---

## Stack e ferramentas

| Camada | Ferramenta |
|---|---|
| Linguagem | Python 3.11 (no container) |
| Empacotamento | Docker + docker-compose |
| Fonte de dados | arXiv API (lib `arxiv`) |
| Bronze | MinIO (S3-compatible) |
| Extração de PDF | PyMuPDF (default), Docling (alternativa selecionável) |
| Vetorização | `sentence-transformers` + [BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3) |
| Ouro — texto | PostgreSQL 16 |
| Ouro — vetores | Qdrant |
| Orquestração | Script Python (`pipeline.py`) |

---

## Estrutura do repositório

```
librarian-orion-ge2026/
├── Dockerfile               # imagem do pipeline (Python 3.11)
├── docker-compose.yml       # 4 serviços: pipeline, minio, postgres, qdrant
├── pipeline.py              # orquestrador end-to-end
├── requirements.txt
├── .env.example             # template das variáveis de ambiente
├── config/
│   └── settings.py          # toda configuração via .env
├── scraper/
│   ├── arxiv_client.py      # busca no arXiv
│   ├── pdf_downloader.py    # download de PDFs + upload Bronze
│   └── deduplicator.py      # dedupe por arxiv_id
├── processing/
│   ├── pdf_extractor.py     # texto limpo a partir dos PDFs
│   ├── chunker.py           # janela 1200/200 com fronteira de sentença
│   └── vectorizer.py        # BGE-M3 via sentence-transformers
└── storage/
    ├── minio_client.py      # Bronze (PDFs + metadados)
    ├── postgres_client.py   # Ouro de texto (papers, chunks, view)
    └── qdrant_client.py     # Ouro de vetores
```

---

## Instalação e uso

### Pré-requisitos

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (ou Docker Engine + docker-compose v2)
- Conexão de internet (a primeira execução baixa o modelo BGE-M3, ~2.3GB, do HuggingFace Hub; depois fica em cache em um volume Docker)

### Setup

```bash
# 1. Clone o repositório
git clone https://github.com/joaopcalumby/librarian-orion-ge2026.git
cd librarian-orion-ge2026

# 2. Copie o exemplo de .env (os defaults batem com o docker-compose)
cp .env.example .env

# 3. Suba os bancos
docker compose up -d minio postgres qdrant

# 4. Construa a imagem do pipeline
docker compose --profile run build pipeline
```

Crie o bucket Bronze pelo console do MinIO (uma vez):

- Acesse <http://localhost:9001>
- Login: `minioadmin` / `minioadmin`
- Crie o bucket `librarian-bronze`

### Rodando o pipeline

```bash
# Busca 3 papers sobre o tema e processa o pipeline completo
docker compose --profile run run --rm pipeline \
  --query "graph neural networks" \
  --n 3
```

Opções da CLI:

- `--query, -q` (obrigatório): tema da busca
- `--n` (default 5): número de papers
- `--extractor` (`pymupdf` ou `docling`, default `pymupdf`): extrator de texto
- `--verbose, -v`: log com nível DEBUG

### Inspecionando os resultados

```bash
# Bronze (PDFs + metadados)
# Console MinIO: http://localhost:9001 → bucket librarian-bronze → sessions/

# Ouro de texto
docker exec -it librarian_postgres \
  psql -U librarian -d librarian \
  -c "SELECT count(*) FROM papers" \
  -c "SELECT count(*) FROM chunks" \
  -c "SELECT chunk_id, arxiv_id, chunk_index FROM chunks_with_meta LIMIT 5"

# Ouro de vetores
curl http://localhost:6333/collections/librarian_chunks

# Vínculo Postgres ↔ Qdrant (use um chunk_id real)
curl http://localhost:6333/collections/librarian_chunks/points/<chunk_id>
```

### Performance esperada

Em **CPU** (caso de uso default do container), uma execução com 3 papers de tamanho médio (~30 páginas cada) leva **~3 a 4 minutos** após o cache do modelo. A vetorização concentra ~85% do tempo. Para acelerar, basta expor uma GPU ao container (requer NVIDIA Container Toolkit no host).

---

## Próximos passos

Itens fora do escopo desta entrega, planejados para as próximas:

1. **Interface Gradio** — campo de busca, chat com o LLM, exibição das citações.
2. **Integração com LLM** — Groq ou HuggingFace Inference API, respostas geradas a partir dos chunks recuperados com citação ao paper original (URL do arXiv).
3. **Retrieval híbrido** — combinar BGE-M3 (semântico) com BM25S (léxico) para reduzir falhas em queries muito específicas.
4. **Sessões efêmeras** — dados deletados automaticamente ao encerrar a sessão do usuário.
5. **Busca incremental** — comando "trazer mais papers sobre o mesmo tema" sem reprocessar os já indexados.
6. **GPU no container** — habilitar CUDA via NVIDIA Container Toolkit para reduzir o tempo de vetorização.

---

## Licença

Projeto acadêmico desenvolvido no contexto do Grupo de Estudos de Engenharia de Dados do Laboratório Orion.
