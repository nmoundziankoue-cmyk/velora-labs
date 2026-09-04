import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


def _normalize_database_url(url: str) -> str:
    """Render (et Heroku historiquement) fournissent DATABASE_URL sous la
    forme `postgres://...` ou `postgresql://...`, sans préciser de driver
    DBAPI. SQLAlchemy a besoin de `postgresql+psycopg://...` pour utiliser
    psycopg (v3, seul driver Postgres présent dans requirements.txt) plutôt
    que le dialecte psycopg2 par défaut."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://") and "+psycopg" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL n'est pas définie ou est vide. Renseigne l'URL de "
        "connexion Postgres avant de lancer le serveur (voir .env.example)."
    )

engine = create_engine(_normalize_database_url(DATABASE_URL), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    """Dépendance FastAPI pour les endpoints synchrones. Pour le thread
    d'indexation en arrière-plan (qui vit au-delà du cycle de la requête),
    utiliser directement `SessionLocal()` — voir main.py, _run_indexing_job."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
