import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """Minimal à dessein : l'Étape 3 (auth) ajoutera les colonnes propres à
    la méthode d'authentification retenue (ex: github_id, email) — pas de
    colonne spéculative ici."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    repos: Mapped[list["Repo"]] = relationship(back_populates="owner")


class Repo(Base):
    __tablename__ = "repos"

    # Cet id EST le repo_id utilisé partout ailleurs (API, Chroma metadata,
    # nom de répertoire de clone) — pas une clé technique séparée.
    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    # Nullable pour l'instant : aucune authentification n'existe encore
    # (Étape 3). Deviendra obligatoire une fois l'auth en place.
    owner_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), ForeignKey("users.id"), nullable=True
    )
    repo_url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    owner: Mapped["User | None"] = relationship(back_populates="repos")
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
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    repo: Mapped["Repo"] = relationship(back_populates="job")
