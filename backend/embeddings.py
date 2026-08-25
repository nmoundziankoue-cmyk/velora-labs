import os
import time

from google import genai
from google.genai import types

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSION = 768

# Absorbe les erreurs transitoires (ex: 503 "model overloaded") sans faire
# planter tout un indexing pour un blip réseau/quota côté Gemini.
RETRY_BACKOFF_SECONDS = (1, 3)


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
            last_error = e

    raise last_error
