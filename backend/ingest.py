import base64
import os
import shutil
import sys
import subprocess

# Défense en profondeur si ce module tourne hors du process main.py
# (ex: script standalone) : évite un crash ascii sur les logs en dessous.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from embeddings import embed_text, QuotaExceededError
from observability import get_logger
from vector_store import add_chunk

logger = get_logger("velora.ingest")

REPOS_DIR = "repos"
CHUNK_SIZE = 1200
CLONE_TIMEOUT_SECONDS = 60

ALLOWED_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".html", ".css", ".json", ".md",
    ".java", ".go", ".rs", ".cpp", ".c",
    ".cs", ".rb", ".php",
    ".yml", ".yaml", ".sql", ".tf", ".kt", ".swift", ".vue",
}

# Fichiers de config/build conventionnellement sans extension : une
# extension seule ne les capte pas, donc whitelist explicite par nom.
ALLOWED_FILENAMES = {"Dockerfile", "Makefile"}

# Dossiers de dépendances/build qui ne contiennent jamais de code source
# pertinent à analyser, mais qui peuvent être committés dans certains repos
# (vendor/, dist/ buildé, etc.) et exploser le nombre de fichiers/chunks.
EXCLUDED_DIRS = {"node_modules", "dist", "build", ".next", "target", "vendor", "venv"}

# Lockfiles générés automatiquement : des milliers de lignes de JSON/YAML
# plat, aucune valeur pour une analyse d'architecture, mais des centaines
# de chunks gaspillés en appels d'embedding.
EXCLUDED_FILENAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Cargo.lock", "poetry.lock",
}

# Un seul fichier généré/minifié massif peut à lui seul produire des
# centaines de chunks alors que MAX_FILES_PER_REPO ne compte que les
# fichiers, pas les chunks.
MAX_FILE_SIZE_BYTES = 300 * 1024


class RepoCloneError(Exception):
    """Levée quand `git clone` échoue (URL invalide, repo privé/inexistant, timeout)."""


def clone_repository(repo_url: str, repo_id: str, access_token: str = None):
    os.makedirs(REPOS_DIR, exist_ok=True)

    repo_path = os.path.join(REPOS_DIR, repo_id)

    cmd = ["git", "clone", "--depth", "1"]
    if access_token:
        # Le token passe en en-tête HTTP git (`-c http.extraHeader=...`),
        # jamais dans l'URL clonée : une URL avec token intégré peut se
        # retrouver telle quelle dans un message d'erreur git (stderr) que
        # quelqu'un serait tenté de logger un jour. Cet en-tête reste local
        # à cet appel `git` uniquement.
        auth_header = base64.b64encode(f"x-access-token:{access_token}".encode()).decode()
        cmd += ["-c", f"http.extraHeader=Authorization: Basic {auth_header}"]
    cmd += [repo_url, repo_path]

    try:
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        shutil.rmtree(repo_path, ignore_errors=True)
        # `from None` : TimeoutExpired.cmd contiendrait le token (argv complet).
        # On casse le chaînage pour qu'il ne puisse jamais apparaître dans un
        # traceback, même indirectement (ex: un futur handler d'erreur qui
        # loggerait __cause__/__context__).
        raise RepoCloneError(
            "Le clone du repository a dépassé le délai autorisé. "
            "Vérifie l'URL ou réessaie plus tard."
        ) from None
    except subprocess.CalledProcessError:
        shutil.rmtree(repo_path, ignore_errors=True)
        # Idem : CalledProcessError.cmd contiendrait le token. On ne relaie
        # ni `from e`, ni `e.stderr`/`e.stdout` (git peut échoer l'URL ou
        # l'en-tête dans son propre message d'erreur).
        raise RepoCloneError(
            "Impossible de cloner le repository. Vérifie que l'URL est correcte, "
            "que le repo existe, et — s'il est privé — que le token fourni a bien "
            "un accès en lecture."
        ) from None

    return repo_path


def get_code_files(repo_path: str):
    """Renvoie (code_files, skipped_files). skipped_files liste les fichiers
    par ailleurs éligibles (bonne extension, pas dans un dossier exclu) mais
    écartés pour dépassement de taille — visible par l'utilisateur final
    (voir main.py) plutôt que silencieusement absent du compte final."""
    code_files = []
    skipped_files = []

    for root, dirs, files in os.walk(repo_path):
        # Élague les dossiers exclus avant d'y descendre : plus rapide que
        # filtrer après coup, et évite de scanner potentiellement des
        # dizaines de milliers de fichiers dans node_modules/.git/etc.
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS and not d.startswith(".")]

        for file in files:
            if file in EXCLUDED_FILENAMES:
                continue

            ext = os.path.splitext(file)[1]
            if ext not in ALLOWED_EXTENSIONS and file not in ALLOWED_FILENAMES:
                continue

            path = os.path.join(root, file)
            relative_path = os.path.relpath(path, repo_path)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue

            if size > MAX_FILE_SIZE_BYTES:
                skipped_files.append({
                    "path": relative_path,
                    "reason": f"File too large ({size // 1024} KB, max "
                    f"{MAX_FILE_SIZE_BYTES // 1024} KB)",
                })
                continue

            code_files.append(path)

    return code_files, skipped_files


def chunk_lines(text: str, chunk_size: int = CHUNK_SIZE):
    """Découpe `text` en chunks alignés sur des frontières de ligne.

    Accumule des lignes consécutives jusqu'à atteindre ~chunk_size
    caractères puis referme le chunk — jamais au milieu d'une ligne, pour
    qu'une citation `line_start`/`line_end` pointe toujours vers des lignes
    entières et exactes du fichier original. Une ligne isolée plus longue
    que chunk_size forme son propre chunk plutôt que d'être tronquée.
    """
    lines = text.splitlines()
    chunks = []
    current_lines = []
    current_len = 0
    start_line = 1

    for i, line in enumerate(lines, start=1):
        line_len = len(line) + 1  # +1 pour le saut de ligne implicite

        if current_lines and current_len + line_len > chunk_size:
            chunks.append({
                "text": "\n".join(current_lines),
                "line_start": start_line,
                "line_end": i - 1,
            })
            current_lines = []
            current_len = 0
            start_line = i

        current_lines.append(line)
        current_len += line_len

    if current_lines:
        chunks.append({
            "text": "\n".join(current_lines),
            "line_start": start_line,
            "line_end": start_line + len(current_lines) - 1,
        })

    return chunks


def index_repository(repo_id: str, repo_path: str, owner_id: str, files: list,
                      known_failures: list = None, on_progress=None):
    """`files` vient de get_code_files() — passé en paramètre plutôt que
    recalculé ici pour éviter un second parcours disque redondant, et pour
    que `known_failures` (fichiers déjà écartés pour taille, voir
    get_code_files) et les échecs rencontrés ici partagent une seule liste
    cohérente.

    `on_progress`, si fourni, est appelé après chaque fichier traité avec
    (indexed_files, total_chunks, files_total, failed_files) — c'est le
    signal de progression réelle utilisé par l'endpoint de statut
    asynchrone (voir main.py, _run_indexing_job), à la place d'un minuteur
    cosmétique. `failed_files` grandit au fil de l'indexation : chaque
    échec (fichier trop volumineux, encodage invalide, erreur Gemini
    persistante, etc.) y apparaît avec son chemin et sa raison — plus
    aucun échec n'est absorbé silencieusement (chaque échec est aussi
    journalisé via `logger`, voir observability.py).
    """
    indexed_files = 0
    total_chunks = 0
    failed_files = list(known_failures or [])
    quota_exceeded = False

    if on_progress and failed_files:
        on_progress(indexed_files, total_chunks, len(files), failed_files)

    for path in files:
        relative_path = os.path.relpath(path, repo_path)

        try:
            # Pas de errors="ignore" : un fichier qui n'est pas du texte
            # UTF-8 valide (binaire mal détecté par son extension, encodage
            # exotique) doit être signalé, pas silencieusement lu en
            # remplaçant les octets invalides — ce qui aurait indexé du
            # contenu corrompu sans que personne ne le sache.
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            if not content.strip():
                continue

            chunks = chunk_lines(content)

            for i, chunk in enumerate(chunks):
                embedding = embed_text(chunk["text"])

                add_chunk(
                    chunk_id=f"{repo_id}:{relative_path}:{i}",
                    text=chunk["text"],
                    embedding=embedding,
                    metadata={
                        "owner_id": owner_id,
                        "repo_id": repo_id,
                        "path": relative_path,
                        "chunk_index": i,
                        "line_start": chunk["line_start"],
                        "line_end": chunk["line_end"],
                    },
                )

                total_chunks += 1

            indexed_files += 1

        except QuotaExceededError:
            # Arrêt immédiat de la boucle : le quota ne va pas se régénérer
            # entre deux fichiers du même job, donc continuer ne ferait
            # qu'accumuler des échecs identiques jusqu'à la fin de la liste.
            # Ce fichier n'est PAS ajouté à failed_files — ce n'est pas un
            # problème propre à lui, c'est un arrêt global du job (voir
            # main.py, qui traite quota_exceeded distinctement de failed_files).
            quota_exceeded = True
            logger.warning(
                f"repo_id={repo_id} quota exceeded while indexing {relative_path} — "
                f"stopping after {indexed_files}/{len(files)} files"
            )
            break
        except UnicodeDecodeError:
            failed_files.append({"path": relative_path, "reason": "File is not valid UTF-8 text"})
            logger.warning(f"repo_id={repo_id} file={relative_path} not valid UTF-8")
        except Exception as e:
            failed_files.append({"path": relative_path, "reason": str(e)})
            logger.warning(f"repo_id={repo_id} file={relative_path} error: {e}")

        if on_progress:
            on_progress(indexed_files, total_chunks, len(files), failed_files)

    return {
        "indexed_files": indexed_files,
        "total_chunks": total_chunks,
        "failed_files": failed_files,
        "quota_exceeded": quota_exceeded,
    }