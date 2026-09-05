import os
import time

from google import genai
from google.genai import types
from google.genai.errors import APIError

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSION = 768

# Absorbe les erreurs transitoires (ex: 503 "model overloaded") sans faire
# planter tout un indexing pour un blip réseau côté Gemini. Ne s'applique
# PAS à un quota épuisé (429/RESOURCE_EXHAUSTED, voir QuotaExceededError
# ci-dessous) : un quota par minute ou par jour ne se régénère pas en
# quelques secondes, retenter avec ce backoff ne ferait que gaspiller un
# quota déjà à zéro et retarder la vraie réponse à l'utilisateur.
RETRY_BACKOFF_SECONDS = (1, 3)


class QuotaExceededError(Exception):
    """Levée immédiatement sur un 429/RESOURCE_EXHAUSTED, sans passer par le
    backoff des erreurs transitoires. À traiter distinctement des échecs
    par fichier — voir ingest.py, index_repository."""


def _is_quota_error(e: Exception) -> bool:
    return isinstance(e, APIError) and (e.code == 429 or e.status == "RESOURCE_EXHAUSTED")


def embed_text(text: str):
    last_error = None

    for delay in (0,) + RETRY_BACKOFF_SECONDS:
        if delay:
            time.sleep(delay)

        try:
            response = client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=text,
                config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMENSION),
            )
            return response.embeddings[0].values
        except Exception as e:
            if _is_quota_error(e):
                raise QuotaExceededError(str(e)) from e
            last_error = e

    raise last_error
