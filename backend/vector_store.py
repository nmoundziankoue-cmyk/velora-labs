import chromadb

client = chromadb.PersistentClient(path="./chroma_data")
collection = client.get_or_create_collection("repo_chunks")


def add_chunk(chunk_id: str, text: str, embedding, metadata: dict):
    collection.add(
        ids=[chunk_id],
        documents=[text],
        embeddings=[embedding],
        metadatas=[metadata],
    )


def search_chunks(query_embedding, top_k=5, repo_id: str = None):
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        where={"repo_id": repo_id} if repo_id else None,
    )

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    return list(zip(documents, metadatas))