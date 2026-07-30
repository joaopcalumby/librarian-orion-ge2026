FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Instalador oficial do uv.
ADD --chmod=755 https://astral.sh/uv/install.sh /install.sh
RUN /install.sh && rm /install.sh
ENV PATH="/root/.local/bin:$PATH"

# Dependências antes do código, para aproveitar cache de camada.
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev

COPY . .

EXPOSE 8008
CMD ["uv", "run", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8008"]
