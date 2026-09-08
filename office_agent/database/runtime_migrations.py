"""Runtime Alembic integration for application startup and health checks."""
from functools import lru_cache
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from . import connection


_BASELINE_REVISION = "001_initial"
_REQUIRED_LEGACY_BASELINE_TABLES = frozenset({
    "user",
    "file",
    "task",
    "agent_config",
    "skill",
    "template",
    "knowledge",
    "execution_log",
})


def _config(database_url: str | None = None) -> Config:
    database_dir = Path(__file__).resolve().parent
    config = Config(str(database_dir / "alembic.ini"))
    config.set_main_option(
        "script_location", str(database_dir / "migrations")
    )
    # ConfigParser treats percent signs as interpolation markers. Escaping here
    # preserves URL-encoded credentials when Alembic later reads the option.
    url = database_url or connection.DATABASE_URL
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


@lru_cache(maxsize=1)
def migration_head() -> str:
    """Return the single authoritative Alembic head revision."""
    head = ScriptDirectory.from_config(_config()).get_current_head()
    if not head:
        raise RuntimeError("Alembic migration head is missing")
    return head


def _current_revision(database_url: str) -> tuple[str | None, set[str]]:
    engine = connection.get_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        with engine.connect() as db_connection:
            revision = MigrationContext.configure(db_connection).get_current_revision()
        return revision, tables
    finally:
        engine.dispose()


def _prepare_unversioned_legacy_database(config: Config, database_url: str) -> None:
    """Attach Alembic to databases created by the historical create_all path.

    Office Agent releases before runtime migration wiring created the complete
    001 table baseline through SQLAlchemy metadata but did not create an
    ``alembic_version`` row. Such databases must start after 001, otherwise
    Alembic attempts to recreate populated tables. We only stamp after proving
    every 001 table exists; incomplete or unrelated databases fail closed.
    Revisions 002-004 are intentionally idempotent so their schema convergence
    still runs against these legacy databases.
    """
    revision, tables = _current_revision(database_url)
    if revision is not None:
        return

    application_tables = tables - {"alembic_version"}
    if not application_tables:
        return

    missing = sorted(_REQUIRED_LEGACY_BASELINE_TABLES - application_tables)
    if missing:
        raise RuntimeError(
            "Unversioned database does not match the Office Agent 001 baseline; "
            f"missing tables: {', '.join(missing)}"
        )
    command.stamp(config, _BASELINE_REVISION)


def upgrade_database(database_url: str | None = None) -> str:
    """Upgrade the authoritative database to Alembic head before services start.

    The default SQLAlchemy singleton is disposed on both sides so no stale
    pooled connection or lazily-bound Session factory survives a schema change.
    Any migration error propagates to the caller and therefore prevents Worker
    startup.
    """
    url = database_url or connection.DATABASE_URL
    config = _config(url)
    connection.dispose_default_engine()
    try:
        _prepare_unversioned_legacy_database(config, url)
        command.upgrade(config, "head")
        revision, _ = _current_revision(url)
        expected = migration_head()
        if revision != expected:
            raise RuntimeError(
                f"Database schema revision mismatch: expected {expected}, got {revision}"
            )
        return expected
    finally:
        connection.dispose_default_engine()
