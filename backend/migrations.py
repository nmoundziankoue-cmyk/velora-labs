"""Migrations de schéma minimales — volontairement PAS Alembic tant qu'on
n'a que des ajouts de colonnes.

`Base.metadata.create_all()` fait `CREATE TABLE IF NOT EXISTS` : il ne
touche jamais à une table déjà existante. Dès qu'une colonne est ajoutée à
un modèle après la première mise en prod, elle n'apparaîtra jamais dans la
vraie table Postgres sans une instruction explicite — d'où ce fichier.

Chaque entrée est idempotente (`ADD COLUMN IF NOT EXISTS`, natif Postgres) :
exécutée une fois au démarrage juste après create_all(), relançable sans
effet ni erreur. Le jour où il faut un changement non-additif (rename,
backfill applicatif, contrainte, suppression), on passe à Alembic.
"""

from sqlalchemy import text

from database import engine
from observability import get_logger

logger = get_logger("velora.migrations")

# (libellé, table, colonne, SQL). Ordre = ordre d'application.
_MIGRATIONS = [
    (
        "jobs.failed_files",
        "jobs",
        "failed_files",
        # Rattrapage : ajoutée au modèle Job après la 1re mise en prod.
        # DEFAULT '[]' pour backfiller d'éventuelles lignes `jobs` déjà
        # présentes (sur une base neuve la colonne existe déjà via
        # create_all(), l'IF NOT EXISTS la saute — la légère divergence de
        # server-default entre les deux chemins est sans effet, SQLAlchemy
        # fournit toujours la valeur à l'INSERT via default=list).
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS failed_files JSONB NOT NULL DEFAULT '[]'::jsonb",
    ),
    (
        "users.stripe_customer_id",
        "users",
        "stripe_customer_id",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR",
    ),
    (
        "users.stripe_subscription_id",
        "users",
        "stripe_subscription_id",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_subscription_id VARCHAR",
    ),
    (
        "users.subscription_status",
        "users",
        "subscription_status",
        # DEFAULT 'none' => toute ligne users déjà présente est backfillée.
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_status VARCHAR NOT NULL DEFAULT 'none'",
    ),
    (
        "users.is_beta",
        "users",
        "is_beta",
        # DEFAULT true => tous les comptes déjà en base passent is_beta=true
        # (beta testeurs actuels grandfathered). Les nouveaux comptes : la
        # valeur sera posée explicitement dans le code de signup à venir.
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_beta BOOLEAN NOT NULL DEFAULT true",
    ),
]


def _column_exists(conn, table: str, column: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).first()
    return row is not None


def run_migrations():
    """Toutes les instructions dans une seule transaction : si l'une échoue,
    rien n'est appliqué (pas d'état partiel)."""
    with engine.begin() as conn:
        for label, table, column, sql in _MIGRATIONS:
            existed = _column_exists(conn, table, column)
            conn.execute(text(sql))
            if existed:
                logger.info(f"migration ok — already present, skipped: {label}")
            else:
                logger.info(f"migration ok — column added: {label}")
