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


def search_chunks(query_embedding, owner_id: str, repo_id: str, top_k=5):
    # Pas de valeur par défaut sur owner_id/repo_id : un appelant qui les
    # oublierait doit obtenir un TypeError immédiat, pas une requête Chroma
    # sans filtre qui retournerait silencieusement les chunks de tout le
    # monde. Les deux sont exigés ensemble (pas repo_id seul) pour qu'un bug
    # applicatif qui confondrait/oublierait l'un des deux ne suffise pas à
    # faire fuiter le contenu d'un autre utilisateur.
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        where={"$and": [{"owner_id": owner_id}, {"repo_id": repo_id}]},
    )

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    return list(zip(documents, metadatas))