import os
from dotenv import load_dotenv

load_dotenv()


ARXIV_DEFAULT_N = int(os.getenv("ARXIV_DEFAULT_N", "5"))
ARXIV_REQUEST_DELAY_SECONDS = float(os.getenv("ARXIV_REQUEST_DELAY_SECONDS", "3.0"))

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET_BRONZE = os.getenv("MINIO_BUCKET_BRONZE", "librarian-bronze")
MINIO_SECURE = os.getenv("MINIO_SECURE", "False").lower() == "true"

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_USER = os.getenv("POSTGRES_USER", "librarian")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "librarian")
POSTGRES_DB = os.getenv("POSTGRES_DB", "librarian")

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "librarian_chunks")

CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1200"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "200"))

BGE_MODEL = os.getenv("BGE_MODEL", "BAAI/bge-m3")
VECTOR_DIM = int(os.getenv("VECTOR_DIM", "1024"))

DATA_DIR = os.getenv("DATA_DIR", "data")
