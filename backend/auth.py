"""Sessions de connexion par cookie opaque — pas de JWT, pas de nouvelle
dépendance de signature de token. Le cookie contient un jeton aléatoire ;
seul son hash SHA-256 est stocké en base (voir models.UserSession)."""

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, Response

from database import SessionLocal
from models import User, UserSession

SESSION_COOKIE_NAME = "velora_session"
SESSION_TTL = timedelta(days=30)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def cross_origin_cookies() -> bool:
    """Frontend et backend sont sur deux domaines différents dès que
    FRONTEND_URL est définie (Vercel + Render) : le cookie de session doit
    alors être SameSite=None + Secure pour être envoyé sur les requêtes
    cross-site du frontend. En local (pas de FRONTEND_URL, localhost:3000 /
    localhost:8000), les deux sont "same-site" au sens SameSite (même
    schéma+hôte, le port n'entre pas en ligne de compte) : Lax suffit et
    Secure casserait tout puisque le dev tourne en http://, pas https://."""
    return bool(os.environ.get("FRONTEND_URL", "").strip())


def create_session(db, user: User) -> str:
    raw_token = secrets.token_urlsafe(32)
    session = UserSession(
        token_hash=_hash_token(raw_token),
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + SESSION_TTL,
    )
    db.add(session)
    db.commit()
    return raw_token


def set_session_cookie(response: Response, raw_token: str) -> None:
    cross_origin = cross_origin_cookies()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        httponly=True,
        secure=cross_origin,
        samesite="none" if cross_origin else "lax",
        max_age=int(SESSION_TTL.total_seconds()),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


def delete_session_by_token(raw_token: str) -> None:
    db = SessionLocal()
    try:
        db.query(UserSession).filter(UserSession.token_hash == _hash_token(raw_token)).delete()
        db.commit()
    finally:
        db.close()


OAUTH_STATE_COOKIE_NAME = "oauth_state"
OAUTH_STATE_MAX_AGE_SECONDS = 600  # le temps de faire l'aller-retour GitHub


def set_oauth_state_cookie(response: Response, state: str) -> None:
    cross_origin = cross_origin_cookies()
    response.set_cookie(
        key=OAUTH_STATE_COOKIE_NAME,
        value=state,
        httponly=True,
        secure=cross_origin,
        samesite="none" if cross_origin else "lax",
        max_age=OAUTH_STATE_MAX_AGE_SECONDS,
        path="/",
    )


def clear_oauth_state_cookie(response: Response) -> None:
    response.delete_cookie(key=OAUTH_STATE_COOKIE_NAME, path="/")


def get_current_user(request: Request) -> User:
    """Auth minimale sans Depends() : appelée explicitement en tête des
    routes protégées, dans le même style que enforce_rate_limit(request)
    ailleurs dans main.py. Lève 401 si le cookie est absent, invalide ou
    expiré."""
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    db = SessionLocal()
    try:
        session = (
            db.query(UserSession)
            .filter(UserSession.token_hash == _hash_token(raw_token))
            .first()
        )
        if session is None or session.expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=401, detail="Not authenticated")

        user = db.query(User).filter(User.id == session.user_id).first()
        if user is None:
            raise HTTPException(status_code=401, detail="Not authenticated")

        # Détacher l'objet de la session DB avant de la fermer, pour que
        # l'appelant puisse encore lire ses attributs (ex: user.id) une fois
        # cette fonction retournée.
        db.expunge(user)
        return user
    finally:
        db.close()
