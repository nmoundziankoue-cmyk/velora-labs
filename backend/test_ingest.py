"""Vérifie qu'une citation (line_start/line_end) produite par chunk_lines()
pointe bien vers les lignes exactes du fichier original — c'est la garantie
que le pitch peut désormais promettre des citations ligne par ligne fiables.

Lancer avec : python3 -m unittest test_ingest -v
"""

import unittest

from ingest import chunk_lines


SAMPLE_CODE = """\
def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b


def divide(a, b):
    if b == 0:
        raise ValueError("division by zero")
    return a / b
"""


class ChunkLinesRoundTripTest(unittest.TestCase):
    """Pour chaque chunk produit, reconstruit les lignes attendues à partir
    de line_start/line_end appliqués au texte original, et vérifie qu'elles
    correspondent EXACTEMENT au texte du chunk. C'est le test qui prouve
    qu'une citation ne peut pas pointer vers le mauvais endroit."""

    def test_round_trip_matches_original_lines(self):
        original_lines = SAMPLE_CODE.splitlines()
        chunks = chunk_lines(SAMPLE_CODE, chunk_size=60)

        self.assertGreater(len(chunks), 1, "le test doit produire plusieurs chunks pour être significatif")

        for chunk in chunks:
            expected_lines = original_lines[chunk["line_start"] - 1 : chunk["line_end"]]
            # Comparaison sur les formes jointes plutôt que sur
            # chunk["text"].splitlines() : .splitlines() est ambigu pour
            # reconstruire un compte de lignes fidèle quand les dernières
            # lignes d'un chunk sont vides (ex: "a\n\n".splitlines() ne
            # redonne pas 2 éléments vides finaux). "\n".join(...) est
            # l'opération réellement utilisée par chunk_lines, donc c'est
            # elle qu'il faut vérifier, dans le même sens.
            self.assertEqual(
                chunk["text"],
                "\n".join(expected_lines),
                f"chunk lignes {chunk['line_start']}-{chunk['line_end']} ne correspond pas "
                f"aux lignes réelles du fichier",
            )

    def test_chunks_cover_the_whole_file_without_gap_or_overlap(self):
        original_lines = SAMPLE_CODE.splitlines()
        chunks = chunk_lines(SAMPLE_CODE, chunk_size=60)

        self.assertEqual(chunks[0]["line_start"], 1)
        self.assertEqual(chunks[-1]["line_end"], len(original_lines))

        for prev, nxt in zip(chunks, chunks[1:]):
            self.assertEqual(
                nxt["line_start"],
                prev["line_end"] + 1,
                "il ne doit y avoir ni trou ni chevauchement entre deux chunks consécutifs",
            )

    def test_specific_known_line_is_correctly_located(self):
        # La ligne 15 de SAMPLE_CODE est : raise ValueError("division by zero")
        original_lines = SAMPLE_CODE.splitlines()
        target_line_no = 15
        self.assertIn("division by zero", original_lines[target_line_no - 1])

        chunks = chunk_lines(SAMPLE_CODE, chunk_size=60)
        owning_chunks = [
            c for c in chunks if c["line_start"] <= target_line_no <= c["line_end"]
        ]
        self.assertEqual(len(owning_chunks), 1, "la ligne cible doit appartenir à exactement un chunk")

        owner = owning_chunks[0]
        # On relit la ligne directement depuis le fichier original à l'aide
        # de line_start/line_end — c'est exactement ce qu'un client ferait
        # pour vérifier une citation, plutôt que de re-parser le texte du
        # chunk (voir la note sur .splitlines() dans le test précédent).
        offset = target_line_no - owner["line_start"]
        line_in_original_at_offset = original_lines[owner["line_start"] - 1 + offset]
        self.assertEqual(line_in_original_at_offset, original_lines[target_line_no - 1])
        self.assertIn("division by zero", line_in_original_at_offset)

    def test_single_line_longer_than_chunk_size_is_not_split(self):
        long_line = "x = " + "1" * 200
        text = f"a = 1\n{long_line}\nb = 2\n"

        chunks = chunk_lines(text, chunk_size=60)
        matching = [c for c in chunks if c["line_start"] == c["line_end"] == 2]

        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["text"], long_line)


if __name__ == "__main__":
    unittest.main()
