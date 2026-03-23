#!/usr/bin/env python3
"""
Minimal MCP server for PostgreSQL — network_aiops database.
Gives Claude direct read-only access to query tables during development.
"""
from __future__ import annotations

import json
import os
import sys

import psycopg
from psycopg.rows import dict_row
from mcp.server.fastmcp import FastMCP

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://admin:admin123@100.118.96.126:5432/network_aiops",
).replace("postgresql+psycopg://", "postgresql://")

mcp = FastMCP("postgres-aiops")


def _connect():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


@mcp.tool()
def query(sql: str) -> str:
    """
    Run a read-only SQL SELECT query against the network_aiops PostgreSQL database.
    Only SELECT statements are allowed. Returns up to 200 rows as JSON.
    """
    stripped = sql.strip().lstrip(";").strip()
    first_word = stripped.split()[0].upper() if stripped else ""
    if first_word not in ("SELECT", "EXPLAIN", "WITH"):
        return json.dumps({"error": "Only SELECT / EXPLAIN / WITH queries are allowed."})
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(stripped)
                rows = cur.fetchmany(200)
                return json.dumps(rows, default=str, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def list_tables() -> str:
    """List all tables and their columns in the network_aiops database."""
    sql = """
        SELECT
            t.table_name,
            array_agg(c.column_name::text ORDER BY c.ordinal_position) AS columns
        FROM information_schema.tables t
        JOIN information_schema.columns c
            ON c.table_name = t.table_name AND c.table_schema = t.table_schema
        WHERE t.table_schema = 'public' AND t.table_type = 'BASE TABLE'
        GROUP BY t.table_name
        ORDER BY t.table_name
    """
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
                return json.dumps(rows, default=str, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def row_counts() -> str:
    """Return approximate row counts for all tables."""
    sql = """
        SELECT
            relname AS table_name,
            reltuples::bigint AS estimated_rows
        FROM pg_class
        JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace
        WHERE nspname = 'public' AND relkind = 'r'
        ORDER BY reltuples DESC
    """
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
                return json.dumps(rows, default=str, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


if __name__ == "__main__":
    mcp.run()
