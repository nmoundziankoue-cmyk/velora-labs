import json
import os
import sys

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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from ingest import clone_repository, get_code_files, index_repository, RepoCloneError
from retrieval import retrieve_context
from llm import generate_answer

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

class AskRequest(BaseModel):
    repo_id: str
    question: str

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

# ===== ROUTES =====

@app.get("/")
def root():
    return {"status": "Velora running 🚀"}

# 🔥 1. INGEST + INDEX AUTO
@app.post("/repo")
def create_repo(req: RepoRequest):

    try:
        repo_id, repo_path = clone_repository(req.repo_url)
    except RepoCloneError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    files = get_code_files(repo_path)

    # 🔥 INDEX DIRECT
    index_result = index_repository(repo_id, repo_path)

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
        "status": "ready"
    }

# 🔥 2. ASK DIRECT
@app.post("/ask")
def ask(req: AskRequest):

    if req.repo_id not in repos:
        return {"error": "repo not found"}

    context, sources = retrieve_context(req.question, repo_id=req.repo_id)

    answer = generate_answer(req.question, context)

    return {
        "answer": answer,
        "sources": sources
    }