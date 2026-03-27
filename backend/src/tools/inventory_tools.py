"""Inventory tools backed by the local CSV inventory."""

import csv
import json
import re
from pathlib import Path

from langchain_core.tools import tool
from src.tools.interface_inventory import resolve_ip_context

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


def _split_tunnel_ips(row: dict) -> list[str]:
    tunnel_ips_raw = row.get("tunnel_ips", "") or ""
    return [ip.strip() for ip in tunnel_ips_raw.split() if ip.strip()]


def normalize_device_role(role: str) -> str:
    """Normalize role text so query aliases match inventory values."""
    return re.sub(r"[\s-]+", "_", (role or "").strip().lower())


def resolve_inventory_role(role: str) -> list[dict]:
    """Return all inventory records whose device role matches *role*."""
    normalized = normalize_device_role(role)
    if not normalized:
        return []
    return [
        dict(row)
        for row in _load_rows()
        if normalize_device_role(str(row.get("device_role", "") or "")) == normalized
    ]


def _row_payload(row: dict) -> dict:
    return {
        "hostname":    row["hostname"],
        "ip_address":  row["ip_address"],
        "os_platform": row["os_platform"],
        "device_role": row["device_role"],
        "site":        row["site"],
        "version":     row["version"],
        "tunnel_ips":  _split_tunnel_ips(row),
    }


def _row_to_json(row: dict, **extras) -> str:
    payload = _row_payload(row)
    payload.update(extras)
    return json.dumps(payload)


def _resolve_exact_interface_owner(ip_value: str) -> dict | None:
    context = resolve_ip_context(ip_value)
    exact_matches = context.get("exact_matches", [])
    if not exact_matches:
        return None

    rows = _load_rows()
    owners: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for interface_row in exact_matches:
        hostname = str(interface_row.get("hostname", "") or "").strip()
        interface_name = str(interface_row.get("interface_name", "") or "").strip()
        if not hostname or (hostname, interface_name) in seen:
            continue
        seen.add((hostname, interface_name))

        inventory_row = next(
            (dict(row) for row in rows if row.get("hostname", "").lower() == hostname.lower()),
            None,
        )
        owners.append({
            "inventory_row": inventory_row,
            "interface": {
                "name": interface_name,
                "ip_address": str(interface_row.get("ip_address", "") or "").strip(),
                "network_cidr": str(interface_row.get("network_cidr", "") or "").strip(),
                "description": str(interface_row.get("description", "") or "").strip(),
                "interface_mode": str(interface_row.get("interface_mode", "") or "").strip(),
                "parent_interface": str(interface_row.get("parent_interface", "") or "").strip(),
            },
        })

    if not owners:
        return None

    unique_hosts = {
        str(item["inventory_row"].get("hostname", "") or item["interface"]["name"])
        for item in owners
        if item.get("inventory_row")
    }
    if len(unique_hosts) > 1:
        return {
            "error": f"Exact interface IP '{ip_value}' maps to multiple device owners.",
            "candidates": [
                {
                    "hostname": item["inventory_row"].get("hostname", ""),
                    "ip_address": item["inventory_row"].get("ip_address", ""),
                    "interface": item["interface"],
                }
                for item in owners
                if item.get("inventory_row")
            ],
        }

    selected = owners[0]
    inventory_row = selected.get("inventory_row")
    if not inventory_row:
        return None
    return {
        **_row_payload(inventory_row),
        "resolved_via": "interface_ip",
        "matched_value": ip_value,
        "matched_interface": selected["interface"],
    }


def resolve_inventory_record(identifier: str) -> dict | None:
    """Resolve a device record by exact hostname, IP, or tunnel IP.

    This helper is intentionally non-tooling so backend execution helpers can
    normalize targets without forcing the LLM to perform another tool round-trip.
    """
    search = (identifier or "").strip()
    if not search:
        return None

    rows = _load_rows()

    if _IP_RE.match(search):
        for row in rows:
            if row["ip_address"] == search:
                return dict(row)
        for row in rows:
            tunnel_ips = _split_tunnel_ips(row)
            if search in tunnel_ips:
                return dict(row)
        return None

    for row in rows:
        if row["hostname"].lower() == search.lower():
            return dict(row)
    return None


@tool
def lookup_device(hostname: str) -> str:
    """Look up a network device by hostname, management IP, tunnel IP, or exact interface IP.

    Accepts:
      - Exact hostname  (e.g. ``HQ-CORE-RT01``)  — case-insensitive
      - IPv4 address    (e.g. ``10.255.1.11``)    — management/tunnel/interface exact match

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
    resolved = resolve_inventory_record(search)
    if resolved:
        resolved_via = "hostname"
        if _IP_RE.match(search):
            tunnel_ips = _split_tunnel_ips(resolved)
            resolved_via = "management_ip" if resolved.get("ip_address") == search else "tunnel_ip" if search in tunnel_ips else "ip"
        return _row_to_json(resolved, resolved_via=resolved_via, matched_value=search)

    if _IP_RE.match(search):
        interface_owner = _resolve_exact_interface_owner(search)
        if interface_owner:
            if "error" in interface_owner:
                return json.dumps(interface_owner)
            return json.dumps(interface_owner)

    # 1. IP address lookup (exact match on device or interface IP columns)
    if _IP_RE.match(search):
        return json.dumps({
            "error":       f"No device or exact interface owner with IP '{search}' in inventory.",
            "suggestions": [r["hostname"] for r in rows],
        })

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
