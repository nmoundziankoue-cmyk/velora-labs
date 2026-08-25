import json
import os
import shutil
import sys
import time
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

    @field_validator("repo_url")
    @classmethod
    def repo_url_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("repo_url must not be empty")
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

# ===== PROTECTION D'USAGE PUBLIC =====
# Démo publique = pas d'auth. Deux garde-fous simples pour éviter qu'un pic
# de trafic ne clone un repo énorme ou n'épuise le quota Gemini gratuit.
MAX_FILES_PER_REPO = 50

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

@app.post("/repo")
def create_repo(req: RepoRequest, request: Request):
    enforce_rate_limit(request)

    try:
        repo_id, repo_path = clone_repository(req.repo_url)
    except RepoCloneError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    files = get_code_files(repo_path)

    if len(files) > MAX_FILES_PER_REPO:
        shutil.rmtree(repo_path, ignore_errors=True)
        return JSONResponse(
            status_code=400,
            content={
                "error": f"Repo too large ({len(files)} files, max {MAX_FILES_PER_REPO} "
                f"for this demo). Try a smaller repo."
            },
        )

    if len(files) == 0:
        shutil.rmtree(repo_path, ignore_errors=True)
        extensions = ", ".join(sorted(ALLOWED_EXTENSIONS))
        return JSONResponse(
            status_code=400,
            content={
                "error": f"No supported source files found in this repository. "
                f"Supported extensions: {extensions}."
            },
        )

    index_result = index_repository(repo_id, repo_path)

    if index_result["total_chunks"] == 0:
        shutil.rmtree(repo_path, ignore_errors=True)
        return JSONResponse(
            status_code=502,
            content={
                "error": "Indexing failed for every file (likely a temporary Gemini "
                "API issue). Try again in a moment."
            },
        )

    repos[repo_id] = {
        "repo_url": req.repo_url,
        "repo_path": repo_path,
        "files": files,
        "indexed": True
    }
    save_repos_state()

    return {
        "repo_id": repo_id,
        "files_found": len(files),
        "indexed_files": index_result["indexed_files"],
        "total_chunks": index_result["total_chunks"],
        "status": "ready"
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