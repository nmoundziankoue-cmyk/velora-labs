"""Monitoring basique : logs structurés (stdlib logging) + Sentry optionnel.

Volontairement pas de format JSON ni de lib de logging tierce (structlog,
python-json-logger) : le stack n'a qu'un seul process/service à ce stade,
un format texte simple avec timestamp/niveau/contexte suffit à être
consultable — le format JSON ne devient vraiment utile qu'avec un
agrégateur de logs en aval, pas encore le cas ici.
"""

import logging
import os
import re

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


class ContextLoggerAdapter(logging.LoggerAdapter):
    """Préfixe chaque message avec les identifiants pertinents (repo_id,
    owner_id...) fournis dans `extra`, en les insérant dans le message
    plutôt que via un format string qui référencerait %(repo_id)s — ça
    planterait (KeyError) sur tout appel de log qui ne les fournit pas."""

    def process(self, msg, kwargs):
        ctx = " ".join(f"{k}={v}" for k, v in self.extra.items() if v is not None)
        return (f"[{ctx}] {msg}" if ctx else msg), kwargs


def get_job_logger(repo_id: str = None, owner_id: str = None) -> logging.LoggerAdapter:
    return ContextLoggerAdapter(get_logger("velora.indexing"), {"repo_id": repo_id, "owner_id": owner_id})


# ===== SENTRY =====
# Optionnel à dessein : contrairement à GEMINI_API_KEY/DATABASE_URL/
# GITHUB_OAUTH_*, l'absence de Sentry ne doit pas empêcher l'app de
# démarrer — c'est de l'observabilité, pas une dépendance fonctionnelle.
# Sans SENTRY_DSN, le monitoring d'erreurs est juste désactivé (log clair
# au démarrage), pas un crash.

_TOKEN_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9]{36,}"),           # PAT GitHub classique
    re.compile(r"github_pat_[A-Za-z0-9_]{60,}"),   # PAT GitHub fine-grained
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),          # Clé API Google/Gemini
]


def _scrub(value):
    if not isinstance(value, str):
        return value
    for pattern in _TOKEN_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    return value


def _scrub_event(event, hint):
    """`before_send` : filtre explicite en plus du scrubbing par défaut de
    Sentry (send_default_pii=False ci-dessous) — un token GitHub ou une clé
    Gemini ne devrait normalement jamais atterrir dans un message
    d'exception (voir POLICY.md), mais mieux vaut une deuxième barrière
    qu'une confiance aveugle dans les réglages par défaut du SDK."""
    if "message" in event:
        event["message"] = _scrub(event["message"])

    for entry in event.get("exception", {}).get("values", []):
        if "value" in entry:
            entry["value"] = _scrub(entry["value"])

    # Pas de scrubbing sur `extra` : ces valeurs sont des types contrôlés
    # (int/str courts) posés explicitement par notre propre code
    # (capture_quota_exceeded ci-dessous), jamais du texte libre externe.
    return event


def init_sentry():
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    logger = get_logger("velora.observability")

    if not dsn:
        logger.info("Sentry disabled: SENTRY_DSN not set.")
        return

    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "development"),
        send_default_pii=False,
        before_send=_scrub_event,
        traces_sample_rate=0,  # pas de tracing de perf à ce stade — juste des erreurs
    )
    logger.info("Sentry monitoring enabled.")


def capture_exception(exc: Exception):
    """À appeler explicitement pour toute exception survenant dans un
    thread d'arrière-plan (_run_indexing_job) : l'auto-instrumentation
    Sentry de FastAPI/Starlette ne couvre que le cycle de vie d'une
    requête HTTP, pas un threading.Thread arbitraire — sans cet appel
    explicite, une exception inattendue dans le thread d'indexation ne
    remonterait nulle part."""
    if not os.environ.get("SENTRY_DSN", "").strip():
        return

    import sentry_sdk

    sentry_sdk.capture_exception(exc)


def capture_quota_exceeded(repo_id: str, owner_id: str, indexed_files: int, files_total: int):
    """Événement Sentry distinct (pas une exception générique) : on veut
    pouvoir suivre dans le temps si le quota Gemini est un problème
    ponctuel ou récurrent une fois les beta testeurs actifs, avec le
    contexte nécessaire pour le diagnostiquer sans recreuser les logs."""
    if not os.environ.get("SENTRY_DSN", "").strip():
        return

    import sentry_sdk

    with sentry_sdk.push_scope() as scope:
        scope.set_tag("event_type", "quota_exceeded")
        scope.set_tag("repo_id", repo_id)
        scope.set_tag("owner_id", owner_id)
        scope.set_extra("indexed_files", indexed_files)
        scope.set_extra("files_total", files_total)
        sentry_sdk.capture_message(
            f"Gemini quota exceeded after {indexed_files}/{files_total} files",
            level="warning",
        )
