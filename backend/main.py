import os
import secrets
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

# Idem pour l'OAuth GitHub (connexion) : sans ces deux valeurs, aucune
# connexion n'est possible, donc autant échouer au démarrage plutôt qu'au
# premier clic sur "Se connecter". Enregistrer une GitHub OAuth App sur
# https://github.com/settings/developers pour les obtenir (étape manuelle,
# voir README.md).
for _var in ("GITHUB_OAUTH_CLIENT_ID", "GITHUB_OAUTH_CLIENT_SECRET"):
    if not os.environ.get(_var, "").strip():
        raise RuntimeError(
            f"{_var} n'est pas définie ou est vide. Enregistre une GitHub OAuth "
            f"App (https://github.com/settings/developers) et renseigne ses "
            f"identifiants avant de lancer le serveur — voir README.md."
        )

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, field_validator
import auth
import github_oauth
from database import Base, SessionLocal, engine
from models import Job, Repo, User
from ingest import ALLOWED_EXTENSIONS, clone_repository, get_code_files, index_repository, RepoCloneError
from retrieval import retrieve_context, RetrievalError
from llm import generate_answer, GenerationError

app = FastAPI(title="Velora API")

# Crée les tables si elles n'existent pas encore. Pas d'Alembic à ce stade :
# le schéma est neuf, sans donnée de prod à faire migrer — `create_all` est
# idempotent et suffisant. À reconsidérer si le schéma doit évoluer alors
# que de vraies données existent déjà.
Base.metadata.create_all(bind=engine)

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

# ===== OAUTH GITHUB =====
# URL publique de CE backend, nécessaire pour construire le redirect_uri de
# l'échange OAuth — doit correspondre EXACTEMENT à l'"Authorization callback
# URL" enregistrée dans les paramètres de la GitHub OAuth App (GitHub refuse
# l'échange sinon). Construit depuis un env var plutôt que depuis l'URL de
# la requête entrante, pour ne pas dépendre d'un header Host potentiellement
# falsifiable derrière un proxy.
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000").strip().rstrip("/")
GITHUB_OAUTH_REDIRECT_URI = f"{BACKEND_URL}/auth/github/callback"
# Après connexion, où renvoyer l'utilisateur : le frontend en prod, sinon
# localhost:3000 en dev — réutilise la même logique que le CORS ci-dessus.
POST_LOGIN_REDIRECT_URL = _frontend_url or "http://localhost:3000"

# ===== MODELS =====

class RepoRequest(BaseModel):
    repo_url: str
    # Personal Access Token GitHub, scope lecture seule, pour les dépôts
    # privés (v1 — voir POLICY.md). Jamais persisté en base : ni sur Repo,
    # ni sur Job. N'existe qu'en mémoire le temps du clone.
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

# ===== INDEXATION ASYNCHRONE =====
# `index_repository()` peut faire des milliers d'appels Gemini séquentiels
# sur un gros repo (plusieurs minutes) — inacceptable dans le cycle d'une
# requête HTTP. On la lance dans un thread d'arrière-plan ; le frontend suit
# la progression réelle via GET /repo/{repo_id}/status (polling), qui lit
# désormais la table `jobs` plutôt qu'un dict en mémoire.
#
# Toujours pas de vraie queue (Celery/RQ + Redis) à ce stade — cf. décision
# de l'étape précédente, inchangée : un thread + Postgres réglent le
# problème réel sans ajouter d'infra de plus que ce que cette étape demande
# déjà (Postgres). Migrable vers une vraie queue plus tard si le volume le
# justifie.
#
# Chaque thread ouvre sa PROPRE session SQLAlchemy (SessionLocal()) : une
# Session n'est pas thread-safe et ne doit jamais être partagée entre
# threads — voir _run_indexing_job.

def _run_indexing_job(repo_id: str, repo_url: str, owner_id: str, access_token: str | None):
    db = SessionLocal()

    def set_job(**fields):
        db.query(Job).filter(Job.repo_id == repo_id).update(fields)
        db.commit()

    try:
        set_job(stage="cloning")

        try:
            repo_path = clone_repository(repo_url, repo_id, access_token=access_token)
        except RepoCloneError as e:
            set_job(stage="error", error=str(e))
            return
        finally:
            # Le token n'a plus d'utilité passé cet appel, qu'il ait réussi
            # ou échoué — jeté immédiatement, pas seulement en fin de job
            # (voir POLICY.md). Il n'a de toute façon jamais été écrit en base.
            access_token = None

        try:
            set_job(stage="reading_files")
            files = get_code_files(repo_path)
            set_job(files_found=len(files))

            if len(files) > MAX_FILES_PER_REPO:
                set_job(
                    stage="error",
                    error=f"Repo too large ({len(files)} files, max {MAX_FILES_PER_REPO} "
                    f"for this demo). Try a smaller repo.",
                )
                return

            if len(files) == 0:
                extensions = ", ".join(sorted(ALLOWED_EXTENSIONS))
                set_job(
                    stage="error",
                    error=f"No supported source files found in this repository. "
                    f"Supported extensions: {extensions}.",
                )
                return

            set_job(stage="indexing")

            def on_progress(indexed_files, total_chunks, files_total):
                set_job(indexed_files=indexed_files, total_chunks=total_chunks)

            index_result = index_repository(repo_id, repo_path, owner_id=owner_id, on_progress=on_progress)

            if index_result["total_chunks"] == 0:
                set_job(
                    stage="error",
                    error="Indexing failed for every file (likely a temporary Gemini "
                    "API issue). Try again in a moment.",
                )
                return

            set_job(
                indexed_files=index_result["indexed_files"],
                total_chunks=index_result["total_chunks"],
                stage="ready",
            )
        finally:
            # Politique de rétention (POLICY.md) : purge garantie, succès ou
            # échec — même principe qu'avant le passage en base de données.
            shutil.rmtree(repo_path, ignore_errors=True)
    finally:
        db.close()

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
# Volontairement toujours en mémoire, pas en base : ce sont des compteurs
# anti-abus jetables (pas de valeur à en garder la trace après coup), et
# écrire en base à chaque requête pour ça serait un coût inutile — cf.
# consigne de l'étape (users/repos/jobs uniquement).

# ===== ROUTES =====

@app.get("/")
def root():
    return {"status": "Velora running 🚀"}

@app.get("/auth/github/login")
def github_login():
    state = secrets.token_urlsafe(16)
    authorize_url = github_oauth.build_authorize_url(state, GITHUB_OAUTH_REDIRECT_URI)

    response = RedirectResponse(authorize_url, status_code=302)
    auth.set_oauth_state_cookie(response, state)
    return response

@app.get("/auth/github/callback")
def github_callback(request: Request, code: str | None = None, state: str | None = None):
    cookie_state = request.cookies.get(auth.OAUTH_STATE_COOKIE_NAME)
    # Comparaison anti-CSRF : le state renvoyé par GitHub doit correspondre
    # exactement à celui posé en cookie avant la redirection — sinon la
    # requête ne vient pas du flux qu'on a initié nous-mêmes.
    if not code or not state or not cookie_state or state != cookie_state:
        raise HTTPException(status_code=400, detail="Invalid or missing OAuth state")

    try:
        access_token = github_oauth.exchange_code_for_token(code, GITHUB_OAUTH_REDIRECT_URI)
        gh_user = github_oauth.fetch_github_user(access_token)
    except github_oauth.GitHubOAuthError as e:
        raise HTTPException(status_code=502, detail=str(e))

    github_id = gh_user.get("id")
    github_login = gh_user.get("login")
    if not github_id or not github_login:
        raise HTTPException(status_code=502, detail="GitHub did not return a usable profile")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.github_id == github_id).first()
        if user is None:
            user = User(github_id=github_id, github_login=github_login)
            db.add(user)
            db.commit()
            db.refresh(user)
        elif user.github_login != github_login:
            # Le login GitHub peut changer ; on le garde à jour pour
            # l'affichage, mais l'identité réelle reste github_id (immuable).
            user.github_login = github_login
            db.commit()
        raw_session_token = auth.create_session(db, user)
    finally:
        db.close()

    response = RedirectResponse(POST_LOGIN_REDIRECT_URL, status_code=302)
    auth.set_session_cookie(response, raw_session_token)
    auth.clear_oauth_state_cookie(response)
    return response

@app.get("/auth/me")
def auth_me(request: Request):
    user = auth.get_current_user(request)
    return {"id": user.id, "github_login": user.github_login}

@app.post("/auth/logout")
def logout(request: Request):
    raw_session_token = request.cookies.get(auth.SESSION_COOKIE_NAME)
    if raw_session_token:
        auth.delete_session_by_token(raw_session_token)

    response = JSONResponse({"status": "logged_out"})
    auth.clear_session_cookie(response)
    return response

def _parse_repo_id(repo_id: str) -> str | None:
    """Valide que repo_id est un UUID avant de l'utiliser dans une requête
    Postgres — sans ça, une valeur malformée fait planter la colonne UUID
    en 500 (DataError) plutôt qu'un 404 propre. Renvoie la forme canonique
    (str) ou None si invalide."""
    try:
        return str(uuid.UUID(repo_id))
    except (ValueError, AttributeError, TypeError):
        return None

@app.post("/repo", status_code=202)
def create_repo(req: RepoRequest, request: Request):
    enforce_rate_limit(request)
    current_user = auth.get_current_user(request)

    db = SessionLocal()
    try:
        # Le Repo et son Job doivent exister en base AVANT de renvoyer
        # repo_id au client, sinon un polling très rapide pourrait taper
        # GET .../status avant le commit — 404 alors que le job est bel et
        # bien en file.
        repo = Repo(repo_url=req.repo_url, owner_id=current_user.id)
        db.add(repo)
        db.flush()
        job = Job(repo_id=repo.id, stage="queued")
        db.add(job)
        db.commit()
        repo_id = repo.id
    finally:
        db.close()

    thread = threading.Thread(
        target=_run_indexing_job,
        args=(repo_id, req.repo_url, current_user.id, req.access_token),
        daemon=True,
    )
    thread.start()

    return {"repo_id": repo_id, "status": "queued"}

@app.get("/repo/{repo_id}/status")
def repo_status(repo_id: str, request: Request):
    current_user = auth.get_current_user(request)

    parsed_id = _parse_repo_id(repo_id)
    if parsed_id is None:
        return JSONResponse(status_code=404, content={"error": "repo not found"})

    db = SessionLocal()
    try:
        repo = db.query(Repo).filter(Repo.id == parsed_id).first()
        job = db.query(Job).filter(Job.repo_id == parsed_id).first() if repo else None
    finally:
        db.close()

    # 404 (jamais 403) si le repo n'existe pas OU n'appartient pas à
    # l'utilisateur courant : ne pas laisser deviner qu'un repo_id existe
    # en révélant une différence de statut entre les deux cas.
    if repo is None or repo.owner_id != current_user.id or job is None:
        return JSONResponse(status_code=404, content={"error": "repo not found"})

    return {
        "repo_id": repo_id,
        "stage": job.stage,
        "files_found": job.files_found,
        "indexed_files": job.indexed_files,
        "total_chunks": job.total_chunks,
        "error": job.error,
    }

@app.post("/ask")
def ask(req: AskRequest, request: Request):
    enforce_rate_limit(request)
    current_user = auth.get_current_user(request)

    parsed_id = _parse_repo_id(req.repo_id)
    if parsed_id is None:
        return JSONResponse(status_code=404, content={"error": "repo not found"})

    db = SessionLocal()
    try:
        repo = db.query(Repo).filter(Repo.id == parsed_id).first()
        job = db.query(Job).filter(Job.repo_id == parsed_id).first() if repo else None
    finally:
        db.close()

    # On exige la propriété ET stage == "ready", pas seulement l'existence
    # du Repo : le Repo est créé dès la mise en file (avant même le clone),
    # donc sans ce contrôle un repo encore en cours d'indexation, en échec,
    # ou appartenant à quelqu'un d'autre renverrait soit une réponse
    # dégradée (0 chunk trouvé), soit une fuite de contenu entre comptes.
    if repo is None or repo.owner_id != current_user.id or job is None or job.stage != "ready":
        return JSONResponse(status_code=404, content={"error": "repo not found"})

    try:
        # parsed_id (forme canonique), pas req.repo_id brut : Chroma a été
        # rempli avec la forme canonique venant de Postgres (repo.id), donc
        # une casse différente mais équivalente dans la requête client ne
        # doit pas faire manquer les chunks. owner_id vient de la session
        # authentifiée (current_user), jamais du corps de la requête client
        # — un client ne peut pas usurper un autre owner_id en le passant
        # lui-même.
        context, sources = retrieve_context(req.question, owner_id=current_user.id, repo_id=parsed_id)
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
