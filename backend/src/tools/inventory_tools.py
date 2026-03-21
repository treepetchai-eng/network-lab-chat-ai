"""Inventory tools backed by the local CSV inventory."""

import csv
import json
import re
from pathlib import Path

from langchain_core.tools import tool

_INVENTORY_PATH = Path(__file__).parent.parent.parent / "inventory" / "inventory.csv"
_IP_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
_ROWS_CACHE: list[dict] | None = None


def _load_rows() -> list[dict]:
    global _ROWS_CACHE
    if _ROWS_CACHE is not None:
        return _ROWS_CACHE
    with open(_INVENTORY_PATH, newline="") as fh:
        _ROWS_CACHE = list(csv.DictReader(fh))
    return _ROWS_CACHE


def _row_to_json(row: dict) -> str:
    return json.dumps({
        "hostname":    row["hostname"],
        "ip_address":  row["ip_address"],
        "os_platform": row["os_platform"],
        "device_role": row["device_role"],
        "site":        row["site"],
        "version":     row["version"],
    })


@tool
def lookup_device(hostname: str) -> str:
    """Look up a network device in the inventory by hostname OR IP address.

    Accepts:
      - Exact hostname  (e.g. ``HQ-CORE-RT01``)  — case-insensitive
      - IPv4 address    (e.g. ``10.255.1.11``)    — exact match

    If no exact match is found, returns suggestions so the caller can retry
    with the correct name.

    Args:
        hostname: Device hostname or IPv4 address to look up.

    Returns:
        JSON string with device details or an error/suggestion payload.
    """
    try:
        rows = _load_rows()
    except FileNotFoundError:
        return json.dumps({"error": f"Inventory file not found: {_INVENTORY_PATH}"})
    except Exception as exc:
        return json.dumps({"error": f"Failed to read inventory: {exc}"})

    search = hostname.strip()

    # 1. IP address lookup (exact match on ip_address column)
    if _IP_RE.match(search):
        for row in rows:
            if row["ip_address"] == search:
                return _row_to_json(row)
        return json.dumps({
            "error":       f"No device with IP '{search}' in inventory.",
            "suggestions": [r["hostname"] for r in rows],
        })

    # 2. Exact hostname match (case-insensitive)
    for row in rows:
        if row["hostname"].lower() == search.lower():
            return _row_to_json(row)

    # 3. Partial hostname match — return suggestions
    suggestions = [r["hostname"] for r in rows if search.lower() in r["hostname"].lower()]
    if not suggestions:
        suggestions = [r["hostname"] for r in rows]

    return json.dumps({
        "error":       f"No exact match for '{hostname}'.",
        "suggestions": suggestions,
    })


@tool
def list_all_devices() -> str:
    """List all devices in the inventory.

    Returns:
        JSON array of all device records in the inventory CSV.
    """
    try:
        return json.dumps(_load_rows())
    except FileNotFoundError:
        return json.dumps({"error": f"Inventory file not found: {_INVENTORY_PATH}"})
    except Exception as exc:
        return json.dumps({"error": f"Failed to read inventory: {exc}"})
