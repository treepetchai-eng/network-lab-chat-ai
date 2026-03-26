from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

import psycopg
from psycopg.rows import dict_row


def _normalize_database_url(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _is_test_runtime() -> bool:
    return "pytest" in sys.modules or bool(os.getenv("PYTEST_CURRENT_TEST"))


def _derive_pytest_database_url(value: str) -> str:
    override = os.getenv("TEST_DATABASE_URL", "").strip() or os.getenv("PYTEST_DATABASE_URL", "").strip()
    if override:
        return _normalize_database_url(override)

    parsed = urlparse(value)
    database_name = parsed.path.lstrip("/") or "network_aiops"
    if database_name.endswith("_pytest"):
        return value
    return urlunparse(parsed._replace(path=f"/{database_name}_pytest"))


def database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("DATABASE_URL is not configured")
    normalized = _normalize_database_url(value)
    if _is_test_runtime():
        return _derive_pytest_database_url(normalized)
    return normalized


@dataclass(frozen=True)
class ParsedDatabaseUrl:
    admin_url: str
    target_url: str
    database_name: str


def parse_database_urls() -> ParsedDatabaseUrl:
    target_url = database_url()
    parsed = urlparse(target_url)
    database_name = parsed.path.lstrip("/") or "network_aiops"
    admin_path = "/postgres"
    admin_url = urlunparse(parsed._replace(path=admin_path))
    return ParsedDatabaseUrl(
        admin_url=admin_url,
        target_url=target_url,
        database_name=database_name,
    )


@contextmanager
def connect(url: str | None = None):
    connection = psycopg.connect(url or database_url(), row_factory=dict_row)
    try:
        yield connection
    finally:
        connection.close()
