# Librarian

![versão](https://img.shields.io/badge/vers%C3%A3o-1.0.5-0b7285)
![python](https://img.shields.io/badge/python-3.11-3776ab)
![docker](https://img.shields.io/badge/docker-compose-2496ed)
![status](https://img.shields.io/badge/status-funcional-2f9e44)

Sistema RAG sobre literatura acadêmica e técnica. O usuário conversa com um agente que responde a partir dos documentos indexados, sempre citando o paper ou artigo de origem com link clicável.

Versão **1.0.5**, que fecha o ciclo do **Grupo de Estudos de Engenharia de Dados do Laboratório Orion** (instrutor: Gean Santos, Maceió/2026).

---

## O problema

Pesquisar literatura técnica tem dois custos que se somam. O primeiro é encontrar o que é relevante dentro de um volume grande de papers. O segundo é confiar no que se leu depois: um resumo sem procedência não serve para citar, embasar um trabalho ou tomar decisão.

Assistentes de linguagem genéricos resolvem o primeiro custo e pioram o segundo, porque respondem com fluência mesmo quando não têm base, e a fonte que apresentam pode não existir.

O Librarian ataca os dois ao mesmo tempo. Ele indexa um corpus que o próprio usuário define, responde apenas a partir desse corpus, e devolve junto de cada resposta o trecho exato e o endereço do documento de onde ele veio. Quando o corpus não responde a pergunta, a resposta correta é dizer isso, não preencher a lacuna.

---

## O que foi construído

Duas aplicações que conversam por HTTP.

**Aplicação interna.** Recebe documentos, extrai o texto, limpa, divide em pedaços, vetoriza e indexa. Expõe uma API HTTP com quatro operações úteis: ingestão de PDF, ingestão de site, busca semântica e listagem das fontes aceitas. Sobe sozinha junto dos bancos.

**Aplicação externa.** O agente conversacional. Recebe a pergunta do usuário, decide quando buscar, chama a busca da aplicação interna, e sintetiza a resposta com citação. Não acessa banco nenhum: fala só HTTP com a interna.

Além disso, um **pipeline batch** que busca papers no arXiv por tema, guarda os PDFs originais numa camada Bronze e processa tudo até o índice, com isolamento de falha por documento.

---

## As exigências que seguimos

O projeto nasceu dentro do Grupo de Estudos, com requisitos definidos pelo instrutor. Todos foram cumpridos.

| Exigência | Como foi atendida |
|---|---|
| Arquitetura medallion (Bronze e Ouro) | Bronze em MinIO com o PDF original e os metadados por sessão; Ouro em PostgreSQL (texto) e Qdrant (vetores) |
| Embeddings com `BAAI/bge-m3`, denso, 1024 dimensões | `processing/vectorizer.py`, via sentence-transformers, com normalização L2 |
| Chunk de 1200 caracteres com sobreposição de 200 | `processing/chunker.py`, com os dois valores configuráveis por variável de ambiente |
| Um documento por requisição | `/pdf` e `/site` aceitam exatamente um documento e devolvem o resultado daquele documento |
| Sites restritos a domínios mapeados | Whitelist em `api/sources.py`; domínio fora dela responde `404` |
| Ligação entre o texto e o vetor | `chunk_id` UUID gerado no chunker, usado como chave primária no Postgres e como id do ponto no Qdrant |
| Empacotamento em Docker junto dos bancos | `docker compose up -d` sobe aplicação e bancos |
| Resiliência por documento | Falha em um paper marca aquele paper e o pipeline continua nos demais |
| Separação entre aplicação interna e externa | A interna não sabe que o agente existe; a externa fala HTTP com ela e pode ser reescrita em qualquer linguagem |
| Orquestração de agente com Agno | `agno/app.py`, com AgentOS servindo a interface e PostgreSQL guardando a memória de sessão |
| Modelo de linguagem | `Groq` do Agno, modelo configurável por variável de ambiente |

A separação entre interna e externa foi pedida para que a entrega do curso não dependesse das partes fora do escopo dele, que eram o modelo de linguagem e a interface. Ela acabou virando a melhor propriedade da arquitetura: a interna sobe e funciona sem o agente, e o agente pode ser trocado sem tocar no índice.

---

## Como foi construído

### Fluxo geral

```
                    ┌──────────────────────────────┐
   usuário  ───────►│  agente (Agno + Groq)        │  :8008
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

### Ingestão

```
documento
  └─► extração de texto (PyMuPDF para PDF, trafilatura para HTML)
      └─► limpeza (dehifenização, números de página, corte em referências)
          └─► chunking (1200 chars, overlap 200, corte em fronteira de sentença)
              └─► vetorização (BAAI/bge-m3, 1024 dim, L2-normalizado)
                  ├─► PostgreSQL: texto e metadados
                  └─► Qdrant: vetores
```

A limpeza importa mais do que parece. PDF de paper vem com cabeçalho institucional repetido em toda página, número de página solto no meio do texto, palavra quebrada com hífen no fim da linha e uma seção de referências que polui a busca com títulos de outros trabalhos. Tudo isso vira ruído dentro do chunk e empurra a similaridade para o lado errado. O extrator remove headers e footers comparando o topo e a base entre páginas, junta as palavras hifenizadas, descarta linhas que são só número e corta o texto quando encontra o cabeçalho de referências.

O chunking usa janela deslizante, mas evita cortar no meio de uma frase: ao chegar no fim da janela, recua até 10% procurando uma fronteira de sentença. Chunk que começa no meio de uma palavra atrapalha tanto o modelo de embedding quanto o humano que vai ler a citação.

### Busca

`POST /search` vetoriza a consulta com **o mesmo modelo da ingestão** e consulta o Qdrant por similaridade de cosseno. Isso não é detalhe: pergunta e corpus precisam viver no mesmo espaço vetorial, senão a similaridade não significa nada.

O Qdrant guarda o vetor e o identificador do documento. O texto e os metadados de citação vêm de um segundo passo, na view `chunks_with_meta`, que faz o join de cada chunk com o título, os autores e a URL do paper. É desse join que sai a citação ancorada.

### O agente

O agente não usa a base de conhecimento embutida do Agno. Ele recebe uma ferramenta, `buscar_documentos`, que chama `POST /search`. O Agno cuida da conversa, da memória de sessão e da interface; a recuperação continua sendo responsabilidade da aplicação interna, que é quem tem o índice e os metadados.

O prompt do sistema fecha o agente em quatro regras: responder só com o que a busca devolveu, citar título e URL ao lado da afirmação, admitir quando o corpus não responde, e nunca inventar título, autor, URL ou número.

---

## Decisões técnicas

| Decisão | Escolha | Motivo |
|---|---|---|
| Fonte primária | arXiv | Acesso aberto, API pública, sem paywall |
| Extração de PDF | PyMuPDF | Rápido e estável em CPU; texto completo em menos de um segundo |
| Extração de HTML | trafilatura | Isola o conteúdo principal e ignora navegação e rodapé |
| Identificador de chunk | UUID gerado no chunker | Postgres e Qdrant compartilham a mesma chave por construção |
| Esquema no Postgres | `papers` + `chunks` + view `chunks_with_meta` | Separa o metadado estável do paper do conteúdo por execução, e a view devolve os dois num lookup só |
| Biblioteca de embedding | sentence-transformers | API estável para carregar o BGE-M3 |
| Python do container | 3.11 | Versão suportada por transformers e torch |
| torch | Wheel do índice CPU do PyTorch | O container roda em CPU; a wheel do PyPI embute o runtime CUDA e leva a imagem de 2.7GB para 9.2GB |
| API assíncrona | FastAPI com `asyncio.to_thread` | Endpoints assíncronos, trabalho pesado de CPU fora do event loop |
| User-Agent no fetch de sites | Browser real | Portais retornam 403 para clientes que se identificam como bot |
| `document_id` sintético | `pdf:<hash>` ou `site:<host>:<hash>` | Permite indexar fonte que não é do arXiv sem mudar o esquema |

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

Documentação interativa em `http://localhost:8000/docs`.

---

## Como rodar

Requer Docker e Docker Compose.

```bash
cp .env.example .env
docker compose up -d
```

Sobem cinco serviços: `api` (8000), `agno` (8008), `postgres` (5432), `qdrant` (6333) e `minio` (9000 e 9001).

Na primeira execução a API baixa o modelo BGE-M3, cerca de 2.3GB, para um volume nomeado. As execuções seguintes reaproveitam esse volume.

Para habilitar o agente, crie o database dele uma única vez e informe a chave do Groq:

```bash
docker exec librarian_postgres psql -U librarian -c "CREATE DATABASE agno"
```

Preencha `GROQ_API_KEY` no `.env` e suba o serviço:

```bash
docker compose up -d agno
```

---

## Como está funcionando

Estado verificado nesta versão, com a stack rodando localmente.

**Serviços.** `api`, `postgres`, `qdrant` e `minio` sobem e permanecem saudáveis. O `agno` sobe com a ferramenta de busca registrada e conectado ao Postgres de memória.

**Ingestão.** Um PDF de paper enviado para `/pdf` é processado até o índice e responde com a contagem de chunks e de vetores, que batem entre si.

**Busca.** Uma consulta sobre um tema presente no corpus retorna os trechos certos com o score de similaridade, o título, os autores e a URL do documento. Consultas de temas diferentes recuperam documentos diferentes: uma pergunta sobre redes neurais em grafos traz os papers do arXiv, uma pergunta introdutória sobre aprendizado de máquina traz o artigo do portal.

**Validação de entrada.** Query vazia responde `400`, `limit` fora da faixa responde `422`, domínio fora da whitelist responde `404`.

**O agente conversa.** Perguntado sobre um paper presente no índice, ele chama `buscar_documentos`, responde em português e cita o título e a URL do documento ao lado da afirmação. Perguntado sobre um paper que não está indexado, ele busca, não encontra e diz que não encontrou, sugerindo reformular a consulta, sem inventar título, autor ou conclusão. As duas regras centrais do prompt do sistema se sustentam na prática.

**Desempenho.** A imagem da aplicação interna tem 2.69GB. A primeira busca depois de subir o container carrega o modelo BGE-M3 e leva por volta de um minuto e meio.

Com o modelo já carregado, a mesma consulta repetida três vezes numa máquina de 8GB de RAM levou 15s, 5.4s e 0.74s. A variação é grande porque a vetorização roda em CPU e disputa recursos com o resto da máquina. Trate a busca como algo entre um e quinze segundos, não como resposta instantânea.

### Verificando por conta própria

Serviços de pé:

```bash
docker compose ps
```

API respondendo:

```bash
curl http://localhost:8000/health
```

Fontes aceitas:

```bash
curl http://localhost:8000/sources
```

Indexando um PDF:

```bash
curl -F "file=@paper.pdf" http://localhost:8000/pdf
```

Buscando:

```bash
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query":"graph neural networks","limit":3}'
```

Conferindo que texto e vetor estão em par:

```bash
docker exec librarian_postgres psql -U librarian -d librarian -c "SELECT count(*) FROM chunks"
```

```bash
curl http://localhost:6333/collections/librarian_chunks
```

Agente de pé, depois de configurar a chave:

```bash
curl http://localhost:8008/health
```

O AgentOS expõe uma API HTTP, não uma interface de chat pronta. As rotas ficam documentadas em `http://localhost:8008/docs`, e `http://localhost:8008/agents` mostra o agente com a ferramenta de busca registrada. Uma interface gráfica é front-end separado, ainda não construído.

Conversando com o agente:

```bash
curl -X POST "http://localhost:8008/agents/bibliotec%C3%A1rio/runs" \
  -F "message=Sobre o que trata o paper MECCH?" \
  -F "stream=false"
```

A resposta traz `content` com o texto e `tools` com as chamadas de ferramenta. Se `tools` vier vazio, o agente respondeu sem consultar o índice — vale ler o `content` inteiro antes de concluir qualquer coisa, porque erro da API do modelo chega por ali.

### Pipeline batch do arXiv

Busca papers sobre um tema, guarda os originais no Bronze e processa tudo até o índice:

```bash
docker compose run --rm --entrypoint python api pipeline.py --query "graph neural networks" --n 5
```

Ao final ele imprime um resumo com os papers processados, os que falharam e em que etapa cada um parou.

---

## Configuração

Tudo vem de variáveis de ambiente, com defaults em `config/settings.py`.

| Variável | Default | Para que serve |
|---|---|---|
| `CHUNK_SIZE` | `1200` | Tamanho do chunk em caracteres |
| `CHUNK_OVERLAP` | `200` | Sobreposição entre chunks consecutivos |
| `BGE_MODEL` | `BAAI/bge-m3` | Modelo de embedding |
| `VECTOR_DIM` | `1024` | Dimensão do vetor, precisa bater com o modelo |
| `QDRANT_COLLECTION` | `librarian_chunks` | Nome da collection |
| `ARXIV_DEFAULT_N` | `5` | Papers por execução do pipeline batch |
| `ARXIV_REQUEST_DELAY_SECONDS` | `3.0` | Intervalo entre downloads, exigido pelo arXiv |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `librarian` | Credenciais e nome do banco do Ouro |
| `POSTGRES_DB_AGNO` | `agno` | Database da memória do agente |
| `GROQ_API_KEY` | — | Chave do modelo do agente |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Modelo de linguagem do agente |

---

## Stack

| Camada | Ferramenta |
|---|---|
| Servidor HTTP | FastAPI + uvicorn |
| Agente | Agno (AgentOS) + Groq |
| Cliente HTTP | httpx |
| Extração de PDF | PyMuPDF |
| Extração de HTML | trafilatura |
| Chunking | implementação própria, janela deslizante com fronteira de sentença |
| Embeddings | `BAAI/bge-m3` via sentence-transformers, em CPU |
| Ouro texto | PostgreSQL 16 |
| Ouro vetor | Qdrant |
| Bronze | MinIO |
| Fonte de papers | API do arXiv |
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
├── config/
│   └── settings.py             # configuração por variável de ambiente
├── scraper/
│   ├── arxiv_client.py         # busca na API do arXiv
│   ├── pdf_downloader.py       # download com retry e validação
│   └── deduplicator.py
├── processing/
│   ├── pdf_extractor.py        # extração e limpeza
│   ├── chunker.py
│   └── vectorizer.py           # BGE-M3
└── storage/
    ├── minio_client.py         # Bronze
    ├── postgres_client.py      # esquema e escrita do Ouro
    └── qdrant_client.py        # collection e busca vetorial
```

---

## Limitações conhecidas

**A busca é apenas semântica.** O retrieval híbrido com BM25S está previsto e não foi implementado. Consulta por termo exato, como nome de sigla ou de autor, depende hoje da similaridade do embedding.

**A vetorização roda em CPU.** É o que domina o tempo de resposta da busca. Usar GPU dentro do container exige o NVIDIA Container Toolkit no host.

**O Medium bloqueia por anti-bot em parte das requisições.** O fingerprint TLS do httpx difere do de um browser real. Contornar exigiria browser headless ou proxy residencial.

**O Bronze só é usado pelo pipeline batch.** O endpoint `/pdf` processa o arquivo em memória e não guarda o original.

**Podem existir chunks órfãos.** Execuções antigas deixaram registros no Postgres sem o vetor correspondente no Qdrant. A busca ignora o que não conseguir hidratar, então isso não quebra nada.

**Não há sessões efêmeras.** Tudo que é indexado permanece. A limpeza por sessão está prevista e não foi implementada.

**Não há interface gráfica.** O agente é consumido pela API do AgentOS. Um front-end de chat está previsto e não foi construído.

**O plano gratuito do Groq limita tokens por minuto, e o teto varia por modelo.** O `llama-3.3-70b-versatile` tem 12 mil por minuto, e outros modelos do catálogo têm 8 mil ou menos. Cada pergunta carrega os trechos recuperados para dentro do prompt, e o histórico da conversa soma a cada turno, então uma sequência de perguntas seguidas pode esbarrar no limite e devolver `rate_limit_exceeded`. As alavancas, se apertar, são reduzir o `limite` de trechos por busca ou encurtar o chunk.

---

## Versão e branches

Esta é a **v1.0.5**, marcada com a tag `v1.0.5`. É a versão que encerra o escopo do Grupo de Estudos.

| Branch | Conteúdo |
|---|---|
| `main` | Versão publicada |
| `dev` | Desenvolvimento |
| `ge` | Entrega congelada do Grupo de Estudos: apenas a aplicação interna, sem busca nem agente |
