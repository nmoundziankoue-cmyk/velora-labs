import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """github_id/github_login ajoutés maintenant que la méthode d'auth est
    tranchée (OAuth GitHub). github_login est purement informatif (affichage
    "connecté en tant que X") — toute la logique d'autorisation repose sur
    `id`/`github_id`, jamais sur le login (qui peut changer)."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    github_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    github_login: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    repos: Mapped[list["Repo"]] = relationship(back_populates="owner")
    sessions: Mapped[list["UserSession"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Repo(Base):
    __tablename__ = "repos"

    # Cet id EST le repo_id utilisé partout ailleurs (API, Chroma metadata,
    # nom de répertoire de clone) — pas une clé technique séparée.
    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    # Obligatoire depuis l'Étape 3 : POST /repo exige désormais un
    # utilisateur authentifié. (Était nullable le temps que l'auth n'existe
    # pas encore — plus de raison de l'être maintenant.)
    owner_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), ForeignKey("users.id"), nullable=False
    )
    repo_url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    owner: Mapped["User"] = relationship(back_populates="repos")
    job: Mapped["Job"] = relationship(back_populates="repo", uselist=False, cascade="all, delete-orphan")


class Job(Base):
    """Statut d'indexation d'un Repo. Un seul job par repo pour l'instant
    (pas de ré-indexation dans ce périmètre) — d'où `unique=True`."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    repo_id: Mapped[str] = mapped_column(
        PG_UUID(as_uuid=False), ForeignKey("repos.id"), nullable=False, unique=True
    )
    # queued | cloning | reading_files | indexing | ready | error
    stage: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    files_found: Mapped[int | None] = mapped_column(Integer, nullable=True)
    indexed_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Liste de {"path": ..., "reason": ...} — fichier trop volumineux,
    # encodage invalide, échec Gemini persistant, etc. Beta prep : rendre
    # visible ce qui échouait avant en silence (voir POLICY.md/README).
    failed_files: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    repo: Mapped["Repo"] = relationship(back_populates="job")


class UserSession(Base):
    """Session de connexion opaque (cookie httponly), pas un JWT — évite
    d'ajouter une lib de signature de token. Seul `token_hash` (SHA-256 du
    jeton réel envoyé au navigateur) est stocké : une fuite de la table
    `sessions` ne suffit pas à réutiliser une session existante."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped["User"] = relationship(back_populates="sessions")
