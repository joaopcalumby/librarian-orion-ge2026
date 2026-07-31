"""
Agente conversacional do Librarian (aplicação externa).

O Agno cuida da conversa, da memória de sessão e da interface. A recuperação
fica com a aplicação interna: o agente chama `POST /search` por uma tool, e é
de lá que vêm os trechos e os metadados de citação.
"""

import os

import httpx
import uvicorn
from agno.agent import Agent
from agno.db.postgres import PostgresDb
from agno.models.groq import Groq
from agno.os import AgentOS
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

LIBRARIAN_API_URL = os.getenv("LIBRARIAN_API_URL", "http://api:8000")
SEARCH_TIMEOUT_SECONDS = float(os.getenv("SEARCH_TIMEOUT_SECONDS", "120"))


def buscar_documentos(consulta: str, limite: int = 5) -> dict:
    """Busca trechos dos documentos indexados na biblioteca.

    Use sempre que a pergunta do usuário depender do conteúdo dos documentos.
    Cada trecho vem com o título, os autores e a URL do documento de origem,
    que devem ser usados para citar a fonte na resposta.

    Args:
        consulta: O tema ou pergunta a buscar, em linguagem natural.
        limite: Quantos trechos retornar (1 a 20). O padrão de 5 costuma bastar.

    Returns:
        Um dicionário com a lista `resultados`. Cada item traz `texto`,
        `titulo`, `autores`, `url` e `score` (quanto maior, mais relevante).
        Em caso de falha, traz a chave `erro` com a explicação.
    """
    try:
        response = httpx.post(
            f"{LIBRARIAN_API_URL}/search",
            json={"query": consulta, "limit": limite},
            timeout=SEARCH_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        # Devolvido como dado, não como exceção: assim o agente consegue
        # explicar a falha ao usuário em vez de interromper a conversa.
        return {"erro": f"não foi possível consultar a biblioteca: {e}"}

    hits = response.json().get("results", [])
    return {
        "resultados": [
            {
                "texto": h["text"],
                "titulo": h["title"],
                "autores": h["authors"],
                "url": h["url"],
                "score": h["score"],
            }
            for h in hits
        ]
    }


SYSTEM_PROMPT = """Você é o bibliotecário do Librarian, um assistente de pesquisa
sobre a literatura técnica indexada nesta biblioteca.

Regras que você não quebra:
- Responda **apenas** com base nos trechos devolvidos por `buscar_documentos`.
  Use a ferramenta antes de responder qualquer pergunta sobre conteúdo.
- Cite sempre a fonte: título do documento e URL, ao lado da afirmação que ela
  sustenta.
- Se os trechos não responderem à pergunta, diga isso claramente e sugira como
  reformular a busca. Não complete a lacuna com conhecimento próprio.
- Não invente título, autor, URL ou número. Se não veio no trecho, não existe.
- Responda em português do Brasil, de forma direta."""


agent = Agent(
    name="Bibliotecário",
    model=Groq(
        id=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        api_key=os.getenv("GROQ_API_KEY"),
    ),
    tools=[buscar_documentos],
    instructions=os.getenv("SYSTEM_PROMPT", SYSTEM_PROMPT),
    db=PostgresDb(db_url=os.getenv("POSTGRES_DB_URL")),
    add_history_to_context=True,
    markdown=True,
)

agent_os = AgentOS(agents=[agent])
app = agent_os.get_app()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# O AgentOS já registra `/health`; um segundo handler no mesmo caminho nunca
# seria alcançado, então não declaramos um.


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8008, reload=True)
