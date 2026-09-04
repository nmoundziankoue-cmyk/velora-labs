"""Client OAuth GitHub minimal — volontairement en stdlib (urllib) plutôt
qu'avec httpx/requests : deux appels HTTP simples ne justifient pas une
nouvelle dépendance PyPI pour ce projet."""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_API_URL = "https://api.github.com/user"

REQUEST_TIMEOUT_SECONDS = 10


class GitHubOAuthError(Exception):
    """Levée quand l'échange de code ou la récupération du profil échoue."""


def _client_id() -> str:
    return os.environ.get("GITHUB_OAUTH_CLIENT_ID", "").strip()


def _client_secret() -> str:
    return os.environ.get("GITHUB_OAUTH_CLIENT_SECRET", "").strip()


def build_authorize_url(state: str, redirect_uri: str) -> str:
    # Scope volontairement vide : ce flux sert uniquement à identifier
    # l'utilisateur (login/id GitHub publics, déjà accessibles via /user
    # avec n'importe quel token de cet utilisateur), pas à accéder à ses
    # dépôts — ça reste le rôle du PAT saisi séparément (voir POLICY.md).
    # Éviter de redemander un scope repo ici évite un deuxième écran de
    # consentement GitHub plus large que nécessaire.
    params = {
        "client_id": _client_id(),
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": "",
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def exchange_code_for_token(code: str, redirect_uri: str) -> str:
    payload = urllib.parse.urlencode({
        "client_id": _client_id(),
        "client_secret": _client_secret(),
        "code": code,
        "redirect_uri": redirect_uri,
    }).encode()

    req = urllib.request.Request(
        TOKEN_URL,
        data=payload,
        headers={"Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as res:
            data = json.loads(res.read().decode())
    except (urllib.error.URLError, TimeoutError) as e:
        raise GitHubOAuthError("Could not reach GitHub to exchange the OAuth code.") from e

    access_token = data.get("access_token")
    if not access_token:
        # GitHub renvoie souvent {"error": "...", "error_description": "..."}
        # plutôt qu'un statut HTTP non-2xx pour un code invalide/expiré.
        raise GitHubOAuthError(data.get("error_description") or "GitHub did not return an access token.")

    return access_token


def fetch_github_user(access_token: str) -> dict:
    req = urllib.request.Request(
        USER_API_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as res:
            return json.loads(res.read().decode())
    except (urllib.error.URLError, TimeoutError) as e:
        raise GitHubOAuthError("Could not reach GitHub to fetch the user profile.") from e
