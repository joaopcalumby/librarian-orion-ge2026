from __future__ import annotations

from urllib.parse import urlparse

ALLOWED_HOSTS: frozenset[str] = frozenset({
    "medium.com",
    "towardsdatascience.com",
    "huggingface.co",
})


def is_allowed(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if host.startswith("www."):
        host = host[4:]
    return host in ALLOWED_HOSTS


def host_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host
