# Imagem do pipeline Librarian. Python 3.11 porque transformers/torch ainda
# têm gaps de compatibilidade com 3.13+. Roda em CPU dentro do container; o
# cache de modelo BGE-M3 é persistido por volume nomeado em docker-compose.
FROM python:3.11-slim

WORKDIR /app

# Dependências de sistema:
# - libgl1, libglib2.0-0: usadas por pymupdf/pdfplumber em rendering.
# - git: alguns pacotes do HuggingFace usam git para cache.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        git \
    && rm -rf /var/lib/apt/lists/*

# Instala dependências antes do código para aproveitar cache de camada.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código do projeto.
COPY config/ ./config/
COPY scraper/ ./scraper/
COPY processing/ ./processing/
COPY storage/ ./storage/
COPY api/ ./api/
COPY pipeline.py .

# Cache do HuggingFace fora do código (volume monta aqui).
ENV HF_HOME=/root/.cache/huggingface

# Default: sobe o servidor FastAPI. Para rodar a CLI batch original,
# sobrescreva o entrypoint: docker run --entrypoint python ... pipeline.py --query ...
EXPOSE 8000
CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8000"]
