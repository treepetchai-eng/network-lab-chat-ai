"""Runtime helpers backed by the generated interface inventory CSVs."""

from __future__ import annotations

import csv
import ipaddress
from pathlib import Path
from typing import Any

_INVENTORY_DIR = Path(__file__).parent.parent.parent / "inventory"
_INTERFACES_PATH = _INVENTORY_DIR / "interfaces.csv"
_INTERFACES_FULL_PATH = _INVENTORY_DIR / "interfaces_full.csv"

_CACHE: dict[tuple[str, bool], dict[str, Any]] = {}
_EMPTY_INDEX = {
    "by_hostname": {},
    "by_ip": {},
    "by_network": {},
    "network_entries": [],
}


def _normalize_row(row: dict[str, str]) -> dict[str, str]:
    return {key: (value or "").strip() for key, value in row.items()}


def _load_interface_rows(*, full: bool = False) -> tuple[list[dict[str, str]], dict[str, Any]]:
    path = _INTERFACES_FULL_PATH if full else _INTERFACES_PATH
    cache_key = (str(path), full)
    try:
        stat = path.stat()
    except FileNotFoundError:
        return [], dict(_EMPTY_INDEX)
    cached = _CACHE.get(cache_key)
    if cached and cached["mtime_ns"] == stat.st_mtime_ns:
        return cached["rows"], cached["index"]

    with open(path, newline="") as fh:
        rows = [_normalize_row(row) for row in csv.DictReader(fh)]

    by_hostname: dict[str, list[dict[str, str]]] = {}
    by_ip: dict[str, list[dict[str, str]]] = {}
    by_network: dict[str, list[dict[str, str]]] = {}
    network_entries: list[tuple[Any, dict[str, str]]] = []

    for row in rows:
        hostname = row.get("hostname", "")
        if hostname:
            by_hostname.setdefault(hostname.lower(), []).append(row)

        ip_address = row.get("ip_address", "")
        if ip_address:
            by_ip.setdefault(ip_address, []).append(row)

        network_cidr = row.get("network_cidr", "")
        if network_cidr:
            by_network.setdefault(network_cidr, []).append(row)
            try:
                network = ipaddress.ip_network(network_cidr, strict=False)
            except ValueError:
                continue
            network_entries.append((network, row))

    network_entries.sort(key=lambda item: item[0].prefixlen, reverse=True)
    index = {
        "by_hostname": by_hostname,
        "by_ip": by_ip,
        "by_network": by_network,
        "network_entries": network_entries,
    }
    _CACHE[cache_key] = {
        "mtime_ns": stat.st_mtime_ns,
        "rows": rows,
        "index": index,
    }
    return rows, index


def interface_inventory_hostnames(*, full: bool = False) -> list[str]:
    """Return known hostnames from the interface inventory."""
    rows, _index = _load_interface_rows(full=full)
    seen: set[str] = set()
    ordered: list[str] = []
    for row in rows:
        hostname = row.get("hostname", "")
        if hostname and hostname not in seen:
            seen.add(hostname)
            ordered.append(hostname)
    return ordered


def find_device_interfaces(hostname: str, *, full: bool = False) -> list[dict[str, str]]:
    """Return all interface rows for one device hostname."""
    search = (hostname or "").strip().lower()
    if not search:
        return []
    _rows, index = _load_interface_rows(full=full)
    return [dict(row) for row in index["by_hostname"].get(search, [])]


def resolve_ip_context(ip_value: str, *, full: bool = False) -> dict[str, Any]:
    """Resolve an IPv4 address to exact interface owners and containing networks."""
    search = (ip_value or "").strip()
    if not search:
        return {"query_ip": "", "exact_matches": [], "network_matches": []}

    try:
        ip_obj = ipaddress.ip_address(search)
    except ValueError:
        return {"query_ip": search, "exact_matches": [], "network_matches": []}

    _rows, index = _load_interface_rows(full=full)
    exact_matches = [dict(row) for row in index["by_ip"].get(search, [])]

    seen_interfaces: set[tuple[str, str]] = set()
    network_matches: list[dict[str, str]] = []
    for network, row in index["network_entries"]:
        if ip_obj not in network:
            continue
        key = (row.get("hostname", ""), row.get("interface_name", ""))
        if key in seen_interfaces:
            continue
        seen_interfaces.add(key)
        network_matches.append(dict(row))

    return {
        "query_ip": search,
        "exact_matches": exact_matches,
        "network_matches": network_matches,
    }


def resolve_prefix_context(prefix: str, *, full: bool = False) -> dict[str, Any]:
    """Resolve a prefix to exact and overlapping interface-network owners."""
    search = (prefix or "").strip()
    if not search:
        return {"query_prefix": "", "normalized_prefix": "", "exact_matches": [], "overlapping_matches": []}

    try:
        network_obj = ipaddress.ip_network(search, strict=False)
    except ValueError:
        return {
            "query_prefix": search,
            "normalized_prefix": "",
            "exact_matches": [],
            "overlapping_matches": [],
        }

    normalized = str(network_obj)
    _rows, index = _load_interface_rows(full=full)
    exact_matches = [dict(row) for row in index["by_network"].get(normalized, [])]

    seen_interfaces: set[tuple[str, str]] = set()
    overlapping_matches: list[dict[str, str]] = []
    for row_network, row in index["network_entries"]:
        if not row_network.overlaps(network_obj):
            continue
        key = (row.get("hostname", ""), row.get("interface_name", ""))
        if key in seen_interfaces:
            continue
        seen_interfaces.add(key)
        overlapping_matches.append(dict(row))

    return {
        "query_prefix": search,
        "normalized_prefix": normalized,
        "exact_matches": exact_matches,
        "overlapping_matches": overlapping_matches,
    }
