import json
import os
import shutil
import sys
import threading
import time
import uuid
from collections import defaultdict

from dotenv import load_dotenv

load_dotenv()

# Force l'UTF-8 sur stdout/stderr : selon la locale du shell, Python peut
# choisir l'ASCII pour ces flux (notamment quand ils sont redirigés vers un
# fichier), ce qui fait planter tout print()/log contenant un caractère
# accentué issu du code indexé (ex: conf.py, setup.py).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# Le SDK google-genai a besoin de GEMINI_API_KEY pour authentifier chaque
# appel embeddings/chat. On échoue immédiatement au démarrage plutôt que de
# laisser chaque requête planter individuellement avec une erreur cryptique.
if not os.environ.get("GEMINI_API_KEY", "").strip():
    raise RuntimeError(
        "GEMINI_API_KEY n'est pas définie ou est vide. Récupère une clé "
        "gratuite sur ai.google.dev et exporte-la avant de lancer le serveur."
    )

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from ingest import ALLOWED_EXTENSIONS, clone_repository, get_code_files, index_repository, RepoCloneError
from retrieval import retrieve_context, RetrievalError
from llm import generate_answer, GenerationError

app = FastAPI(title="Velora API")

# ===== CORS =====
# Frontend (Vercel) et backend (Render) sont deux origines différentes en
# prod. localhost:3000 reste toujours autorisé pour le dev local. En prod,
# définir FRONTEND_URL (ex: "https://velora-labs.vercel.app") dans les
# variables d'env Render — pas besoin de modifier ce fichier à chaque deploy.
_allowed_origins = ["http://localhost:3000"]
_frontend_url = os.environ.get("FRONTEND_URL", "").strip()
if _frontend_url:
    _allowed_origins.append(_frontend_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== MODELS =====

class RepoRequest(BaseModel):
    repo_url: str
    # Personal Access Token GitHub, scope lecture seule, pour les dépôts
    # privés (v1 — voir POLICY.md). Jamais persisté : ni dans repos_state.json,
    # ni loggé. N'existe qu'en mémoire le temps de cette requête.
    access_token: str | None = None

    @field_validator("repo_url")
    @classmethod
    def repo_url_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("repo_url must not be empty")
        return v

    @field_validator("access_token")
    @classmethod
    def access_token_stripped_or_none(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        # Garde-fou minimal, pas une validation de format (les formats de
        # token GitHub évoluent) : un espace/saut de ligne interne trahit
        # presque toujours une erreur de copier-coller (texte en trop, deux
        # tokens collés), pas un token valide.
        if any(c.isspace() for c in v):
            raise ValueError("access_token must not contain spaces or newlines")
        return v

class AskRequest(BaseModel):
    repo_id: str
    question: str

    @field_validator("question")
    @classmethod
    def question_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("question must not be empty")
        return v

# ===== STATE (persisté dans un fichier JSON) =====
REPOS_STATE_FILE = "repos_state.json"

def load_repos_state():
    if os.path.exists(REPOS_STATE_FILE):
        with open(REPOS_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_repos_state():
    with open(REPOS_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(repos, f, indent=2)

repos = load_repos_state()

# ===== INDEXATION ASYNCHRONE =====
# `index_repository()` peut faire des milliers d'appels Gemini séquentiels
# sur un gros repo (plusieurs minutes) — inacceptable dans le cycle d'une
# requête HTTP. On la lance dans un thread d'arrière-plan et le frontend
# suit la progression réelle via GET /repo/{repo_id}/status (polling).
#
# Volontairement pas de vraie queue (Celery/RQ + Redis) à ce stade : le
# projet tourne en un seul process, sans dépendance externe, et Render en
# plan gratuit ne supporte pas facilement un service worker séparé + Redis
# managé. Un thread + un dict en mémoire réglent le problème réel (timeout
# HTTP, progression trompeuse) sans ajouter d'infra — migrable plus tard si
# le volume le justifie. Limite acceptée : ne survit pas à un redémarrage du
# process, ne scale pas à plusieurs workers — déjà vrai de `repos` aujourd'hui.
_index_jobs = {}

def _run_indexing_job(repo_id: str, repo_url: str, access_token: str | None):
    _index_jobs[repo_id]["stage"] = "cloning"

    try:
        repo_path = clone_repository(repo_url, repo_id, access_token=access_token)
    except RepoCloneError as e:
        _index_jobs[repo_id]["stage"] = "error"
        _index_jobs[repo_id]["error"] = str(e)
        return
    finally:
        # Le token n'a plus d'utilité passé cet appel, qu'il ait réussi ou
        # échoué — jeté immédiatement, pas seulement en fin de job (voir
        # POLICY.md).
        access_token = None

    try:
        _index_jobs[repo_id]["stage"] = "reading_files"
        files = get_code_files(repo_path)
        _index_jobs[repo_id]["files_found"] = len(files)

        if len(files) > MAX_FILES_PER_REPO:
            _index_jobs[repo_id]["stage"] = "error"
            _index_jobs[repo_id]["error"] = (
                f"Repo too large ({len(files)} files, max {MAX_FILES_PER_REPO} "
                f"for this demo). Try a smaller repo."
            )
            return

        if len(files) == 0:
            extensions = ", ".join(sorted(ALLOWED_EXTENSIONS))
            _index_jobs[repo_id]["stage"] = "error"
            _index_jobs[repo_id]["error"] = (
                f"No supported source files found in this repository. "
                f"Supported extensions: {extensions}."
            )
            return

        _index_jobs[repo_id]["stage"] = "indexing"

        def on_progress(indexed_files, total_chunks, files_total):
            _index_jobs[repo_id]["indexed_files"] = indexed_files
            _index_jobs[repo_id]["total_chunks"] = total_chunks

        index_result = index_repository(repo_id, repo_path, on_progress=on_progress)

        if index_result["total_chunks"] == 0:
            _index_jobs[repo_id]["stage"] = "error"
            _index_jobs[repo_id]["error"] = (
                "Indexing failed for every file (likely a temporary Gemini "
                "API issue). Try again in a moment."
            )
            return

        repos[repo_id] = {
            "repo_url": repo_url,
            "files": files,
            "indexed": True,
        }
        save_repos_state()

        _index_jobs[repo_id]["indexed_files"] = index_result["indexed_files"]
        _index_jobs[repo_id]["total_chunks"] = index_result["total_chunks"]
        _index_jobs[repo_id]["stage"] = "ready"
    finally:
        # Politique de rétention (POLICY.md) : purge garantie, succès ou
        # échec — même principe qu'avant le passage en asynchrone.
        shutil.rmtree(repo_path, ignore_errors=True)

# ===== PROTECTION D'USAGE PUBLIC =====
# Démo publique = pas d'auth. Deux garde-fous simples pour éviter qu'un pic
# de trafic ne clone un repo énorme ou n'épuise le quota Gemini gratuit.
MAX_FILES_PER_REPO = 1000

RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 20
_rate_limit_hits = defaultdict(list)

def enforce_rate_limit(request: Request):
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    hits = _rate_limit_hits[ip]
    hits[:] = [t for t in hits if now - t < RATE_LIMIT_WINDOW_SECONDS]
    if len(hits) >= RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Limit: {RATE_LIMIT_MAX_REQUESTS} per {RATE_LIMIT_WINDOW_SECONDS}s. Try again shortly.",
        )
    hits.append(now)

# ===== ROUTES =====

@app.get("/")
def root():
    return {"status": "Velora running 🚀"}

@app.post("/repo", status_code=202)
def create_repo(req: RepoRequest, request: Request):
    enforce_rate_limit(request)

    repo_id = str(uuid.uuid4())
    # Le job doit exister dans _index_jobs AVANT de renvoyer repo_id au
    # client, sinon un polling très rapide pourrait taper GET .../status
    # avant que le thread n'ait eu le temps de s'enregistrer lui-même — 404
    # alors que le job est bel et bien en file. On l'initialise donc ici,
    # de façon synchrone, pas dans _run_indexing_job.
    _index_jobs[repo_id] = {
        "stage": "queued",
        "files_found": None,
        "indexed_files": 0,
        "total_chunks": 0,
        "error": None,
    }

    thread = threading.Thread(
        target=_run_indexing_job,
        args=(repo_id, req.repo_url, req.access_token),
        daemon=True,
    )
    thread.start()

    return {"repo_id": repo_id, "status": "queued"}

@app.get("/repo/{repo_id}/status")
def repo_status(repo_id: str):
    job = _index_jobs.get(repo_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": "repo not found"})

    return {
        "repo_id": repo_id,
        "stage": job["stage"],
        "files_found": job["files_found"],
        "indexed_files": job["indexed_files"],
        "total_chunks": job["total_chunks"],
        "error": job["error"],
    }

@app.post("/ask")
def ask(req: AskRequest, request: Request):
    enforce_rate_limit(request)

    if req.repo_id not in repos:
        return JSONResponse(status_code=404, content={"error": "repo not found"})

    try:
        context, sources = retrieve_context(req.question, repo_id=req.repo_id)
        answer = generate_answer(req.question, context)
    except (RetrievalError, GenerationError) as e:
        return JSONResponse(
            status_code=502,
            content={"error": f"AI service is temporarily unavailable: {str(e)}"},
        )

    return {
        "answer": answer,
        "sources": sources
    }