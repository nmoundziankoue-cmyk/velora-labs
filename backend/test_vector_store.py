"""Vérifie que search_chunks() ne peut jamais retourner un fragment
appartenant à un autre owner_id/repo_id — y compris quand un appelant se
trompe et ne fait matcher qu'un seul des deux (le scénario "bug de filtre
côté application" que ce test doit couvrir). Tourne contre la vraie base
ChromaDB locale, pas un mock : c'est une garantie de stockage qu'on veut
vérifier, pas un comportement simulé.

vector_store.py n'a aucune dépendance à Gemini, donc ce test n'a pas besoin
de GEMINI_API_KEY : python3 -m unittest test_vector_store -v
"""

import unittest
import uuid

from vector_store import add_chunk, collection, search_chunks

FAKE_EMBEDDING = [0.1] * 768


class ChromaOwnerIsolationTest(unittest.TestCase):
    def setUp(self):
        # Identifiants jetables et uniques par exécution : jamais de
        # collision avec de vraies données ni avec un run précédent.
        self.owner_a = f"test-owner-a-{uuid.uuid4()}"
        self.owner_b = f"test-owner-b-{uuid.uuid4()}"
        self.repo_a = f"test-repo-a-{uuid.uuid4()}"
        self.repo_b = f"test-repo-b-{uuid.uuid4()}"
        self.chunk_ids = []

        self._add(self.owner_a, self.repo_a, "alice-secret-content")
        self._add(self.owner_b, self.repo_b, "bob-secret-content")

    def tearDown(self):
        collection.delete(ids=self.chunk_ids)

    def _add(self, owner_id, repo_id, text):
        chunk_id = f"{owner_id}:{repo_id}:0"
        self.chunk_ids.append(chunk_id)
        add_chunk(
            chunk_id=chunk_id,
            text=text,
            embedding=FAKE_EMBEDDING,
            metadata={
                "owner_id": owner_id,
                "repo_id": repo_id,
                "path": "f.py",
                "chunk_index": 0,
                "line_start": 1,
                "line_end": 1,
            },
        )

    def test_correct_owner_and_repo_returns_own_content(self):
        results = search_chunks(FAKE_EMBEDDING, owner_id=self.owner_a, repo_id=self.repo_a)
        docs = [doc for doc, _ in results]
        self.assertIn("alice-secret-content", docs)
        self.assertNotIn("bob-secret-content", docs)

    def test_wrong_owner_id_with_correct_repo_id_returns_nothing(self):
        # Bug applicatif simulé : bon repo_id, mauvais owner_id (ex: contexte
        # utilisateur mal propagé). Le double filtre doit bloquer ça net.
        results = search_chunks(FAKE_EMBEDDING, owner_id=self.owner_b, repo_id=self.repo_a)
        self.assertEqual(results, [])

    def test_wrong_repo_id_with_correct_owner_id_returns_nothing(self):
        results = search_chunks(FAKE_EMBEDDING, owner_id=self.owner_a, repo_id=self.repo_b)
        self.assertEqual(results, [])

    def test_cross_user_content_never_leaks_either_direction(self):
        results_a = search_chunks(FAKE_EMBEDDING, owner_id=self.owner_a, repo_id=self.repo_a)
        results_b = search_chunks(FAKE_EMBEDDING, owner_id=self.owner_b, repo_id=self.repo_b)
        docs_a = [doc for doc, _ in results_a]
        docs_b = [doc for doc, _ in results_b]
        self.assertNotIn("bob-secret-content", docs_a)
        self.assertNotIn("alice-secret-content", docs_b)

    def test_missing_owner_id_or_repo_id_raises_immediately(self):
        # Pas de valeur par défaut sur owner_id/repo_id : un appelant qui en
        # oublie un doit obtenir un TypeError, jamais une requête Chroma
        # sans filtre qui retournerait le contenu de tout le monde.
        with self.assertRaises(TypeError):
            search_chunks(FAKE_EMBEDDING, repo_id=self.repo_a)
        with self.assertRaises(TypeError):
            search_chunks(FAKE_EMBEDDING, owner_id=self.owner_a)


if __name__ == "__main__":
    unittest.main()
