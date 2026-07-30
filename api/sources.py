from __future__ import annotations

from urllib.parse import urlparse

ALLOWED_HOSTS: frozenset[str] = frozenset({
    "medium.com",
    "towardsdatascience.com",
    "huggingface.co",
})


def host_of(url: str) -> str:
    """Host da URL em minúsculas, sem o prefixo `www.`. String vazia se não parsear."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def is_allowed(url: str) -> bool:
    return host_of(url) in ALLOWED_HOSTS
