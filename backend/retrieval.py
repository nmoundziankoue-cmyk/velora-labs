from embeddings import embed_text
from vector_store import search_chunks


def retrieve_context(question: str, repo_id: str = None):
    try:
        # 1. Embedding de la question
        query_embedding = embed_text(question)

        # 2. Recherche dans la base vectorielle
        results = search_chunks(query_embedding, repo_id=repo_id)

        context_parts = []
        sources = []

        # 3. Construire contexte + sources
        for doc, meta in results:
            path = meta.get("path", "unknown")
            chunk_index = meta.get("chunk_index", 0)

            context_parts.append(
                f"FILE: {path}\nCHUNK: {chunk_index}\nCONTENT:\n{doc}"
            )

            sources.append({
                "path": path,
                "chunk_index": chunk_index
            })

        # 4. Fusion contexte
        context = "\n\n---\n\n".join(context_parts)

        return context, sources

    except Exception as e:
        return f"Error retrieving context: {str(e)}", []