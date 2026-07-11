import os
import shutil
import sys
import uuid
import subprocess

# Défense en profondeur si ce module tourne hors du process main.py
# (ex: script standalone) : évite un crash ascii sur print() en dessous.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from embeddings import embed_text
from vector_store import add_chunk

REPOS_DIR = "repos"
CHUNK_SIZE = 1200
CLONE_TIMEOUT_SECONDS = 60

ALLOWED_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".html", ".css", ".json", ".md",
    ".java", ".go", ".rs", ".cpp", ".c",
    ".cs", ".rb", ".php"
}


class RepoCloneError(Exception):
    """Levée quand `git clone` échoue (URL invalide, repo privé/inexistant, timeout)."""


def clone_repository(repo_url: str):
    os.makedirs(REPOS_DIR, exist_ok=True)

    repo_id = str(uuid.uuid4())
    repo_path = os.path.join(REPOS_DIR, repo_id)

    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, repo_path],
            check=True,
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        shutil.rmtree(repo_path, ignore_errors=True)
        raise RepoCloneError(
            "Le clone du repository a dépassé le délai autorisé. "
            "Vérifie l'URL ou réessaie plus tard."
        )
    except subprocess.CalledProcessError as e:
        print(f"[DEBUG clone_repository] str(e)={e!r}", flush=True)
        print(f"[DEBUG clone_repository] e.returncode={e.returncode}", flush=True)
        print(f"[DEBUG clone_repository] e.stderr={e.stderr!r}", flush=True)
        print(f"[DEBUG clone_repository] e.stdout={e.stdout!r}", flush=True)
        shutil.rmtree(repo_path, ignore_errors=True)
        raise RepoCloneError(
            "Impossible de cloner le repository. Vérifie que l'URL est "
            "correcte et que le repo est public."
        ) from e

    return repo_id, repo_path


def get_code_files(repo_path: str):
    code_files = []

    for root, _, files in os.walk(repo_path):
        for file in files:
            ext = os.path.splitext(file)[1]
            if ext in ALLOWED_EXTENSIONS:
                code_files.append(os.path.join(root, file))

    return code_files


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE):
    chunks = []
    start = 0

    while start < len(text):
        chunk = text[start:start + chunk_size]
        chunks.append(chunk)
        start += chunk_size

    return chunks


def index_repository(repo_id: str, repo_path: str):
    files = get_code_files(repo_path)
    indexed_files = 0
    total_chunks = 0

    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            if not content.strip():
                continue

            chunks = chunk_text(content)

            for i, chunk in enumerate(chunks):
                embedding = embed_text(chunk)

                add_chunk(
                    chunk_id=f"{repo_id}:{path}:{i}",
                    text=chunk,
                    embedding=embedding,
                    metadata={
                        "repo_id": repo_id,
                        "path": path,
                        "chunk_index": i,
                    },
                )

                total_chunks += 1

            indexed_files += 1

        except Exception as e:
            print(f"Error indexing {path}: {e}", flush=True)

    return {
        "indexed_files": indexed_files,
        "total_chunks": total_chunks,
    }