"""Database configuration for the operations platform."""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

load_dotenv()

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_DB_PATH = _BACKEND_ROOT / "data" / "network_ops_ai.db"
_DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
_ALEMBIC_INI = _BACKEND_ROOT / "alembic.ini"
_ALEMBIC_SCRIPT_LOCATION = _BACKEND_ROOT / "migrations"

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{_DEFAULT_DB_PATH}")


class Base(DeclarativeBase):
    """Declarative base for SQLAlchemy ORM models."""


engine = create_engine(
    DATABASE_URL,
    future=True,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def _managed_table_names() -> set[str]:
    from src.ops import models  # noqa: F401  - ensure metadata is imported

    return set(Base.metadata.tables.keys())


def _determine_bootstrap_action(
    *,
    existing_tables: set[str],
    managed_tables: set[str],
) -> Literal["upgrade", "stamp_existing", "error_partial_schema"]:
    if "alembic_version" in existing_tables:
        return "upgrade"
    managed_existing = existing_tables.intersection(managed_tables)
    if managed_existing == managed_tables and managed_existing:
        return "stamp_existing"
    if managed_existing:
        return "error_partial_schema"
    return "upgrade"


def _alembic_config():
    try:
        from alembic.config import Config
    except ImportError as exc:  # pragma: no cover - exercised in runtime only
        raise RuntimeError(
            "Alembic is not installed. Install backend requirements before starting the API."
        ) from exc

    if not _ALEMBIC_INI.exists():
        raise RuntimeError(f"Alembic config not found at {_ALEMBIC_INI}")
    if not _ALEMBIC_SCRIPT_LOCATION.exists():
        raise RuntimeError(f"Alembic migrations directory not found at {_ALEMBIC_SCRIPT_LOCATION}")

    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("script_location", str(_ALEMBIC_SCRIPT_LOCATION))
    config.set_main_option("sqlalchemy.url", DATABASE_URL)
    return config


def run_migrations(*, stamp_existing: bool = False) -> None:
    """Apply Alembic migrations or stamp an already-provisioned schema."""
    from alembic import command

    config = _alembic_config()
    if stamp_existing:
        command.stamp(config, "head")
        return
    command.upgrade(config, "head")


def init_db() -> None:
    """Initialize the operations schema using Alembic migrations by default."""
    managed_tables = _managed_table_names()
    bootstrap_mode = os.getenv("OPS_DB_BOOTSTRAP_MODE", "migrate").strip().lower() or "migrate"

    if bootstrap_mode == "create_all":
        Base.metadata.create_all(bind=engine)
        return

    if bootstrap_mode != "migrate":
        raise RuntimeError(
            f"Unsupported OPS_DB_BOOTSTRAP_MODE='{bootstrap_mode}'. Expected 'migrate' or 'create_all'."
        )

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    action = _determine_bootstrap_action(
        existing_tables=existing_tables,
        managed_tables=managed_tables,
    )
    if action == "error_partial_schema":
        raise RuntimeError(
            "Detected a partial operations schema without alembic metadata. "
            "Refusing to auto-stamp because the database may be out of sync with the code."
        )
    run_migrations(stamp_existing=action == "stamp_existing")


def create_all_for_testing() -> None:
    """Create all known tables directly for tests and ephemeral local helpers."""
    Base.metadata.create_all(bind=engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional scope for DB work."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
