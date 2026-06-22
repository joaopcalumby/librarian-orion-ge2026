"""
Whitelist de domínios mapeados para o endpoint /site.

Cada entrada representa um domínio cujo conteúdo principal é entregue no
HTML da página (não escondido em SPA com JS), e portanto pode ser extraído
via fetch HTTP + parser. Sites fora da whitelist retornam 404.

Para adicionar um novo site basta incluir o host (sem `www.`) na constante
`ALLOWED_HOSTS` abaixo.
"""

from __future__ import annotations

from urllib.parse import urlparse

ALLOWED_HOSTS: frozenset[str] = frozenset({
    "medium.com",
    "towardsdatascience.com",
    "huggingface.co",
})


def is_allowed(url: str) -> bool:
    """True se o host (normalizado, sem 'www.') está na whitelist."""
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
