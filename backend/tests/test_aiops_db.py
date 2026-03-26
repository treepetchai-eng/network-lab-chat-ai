from __future__ import annotations

from src.aiops import db as db_module


def test_database_url_uses_pytest_suffix(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@db.example:5432/network_aiops")
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("PYTEST_DATABASE_URL", raising=False)
    monkeypatch.setattr(db_module, "_is_test_runtime", lambda: True)

    assert db_module.database_url() == "postgresql://user:pass@db.example:5432/network_aiops_pytest"


def test_database_url_uses_explicit_test_override(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@db.example:5432/network_aiops")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+psycopg://user:pass@db.example:5432/custom_aiops_test")
    monkeypatch.delenv("PYTEST_DATABASE_URL", raising=False)
    monkeypatch.setattr(db_module, "_is_test_runtime", lambda: True)

    assert db_module.database_url() == "postgresql://user:pass@db.example:5432/custom_aiops_test"


def test_parse_database_urls_tracks_pytest_target(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db.example:5432/network_aiops")
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("PYTEST_DATABASE_URL", raising=False)
    monkeypatch.setattr(db_module, "_is_test_runtime", lambda: True)

    urls = db_module.parse_database_urls()

    assert urls.target_url == "postgresql://user:pass@db.example:5432/network_aiops_pytest"
    assert urls.admin_url == "postgresql://user:pass@db.example:5432/postgres"
    assert urls.database_name == "network_aiops_pytest"
