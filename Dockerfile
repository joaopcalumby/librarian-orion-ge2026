# Imagem da aplicação interna do Librarian: servidor HTTP + pipeline batch.
# Python 3.11 é a versão suportada por transformers/torch. Roda em CPU; o
# modelo BGE-M3 é persistido por volume nomeado em docker-compose.
FROM python:3.11-slim

WORKDIR /app

# Dependências de sistema:
# - libgl1, libglib2.0-0: usadas por pymupdf em rendering.
# - git: alguns pacotes do HuggingFace usam git para cache.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        git \
    && rm -rf /var/lib/apt/lists/*

# torch vem do índice CPU do PyTorch: a wheel do PyPI embute o runtime CUDA,
# que este container não usa. Fica em camada própria, antes do requirements,
# para que mudança de dependência não dispare novo download de ~2GB.
RUN pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        --extra-index-url https://pypi.org/simple \
        "torch>=2.2.0"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config/ ./config/
COPY scraper/ ./scraper/
COPY processing/ ./processing/
COPY storage/ ./storage/
COPY api/ ./api/
COPY pipeline.py .

# Cache do HuggingFace fora do código (volume monta aqui).
ENV HF_HOME=/root/.cache/huggingface

# Default: sobe o servidor HTTP. O pipeline batch continua acessível com
# `docker compose run --rm --entrypoint python api pipeline.py --query ...`.
EXPOSE 8000
CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8000"]
