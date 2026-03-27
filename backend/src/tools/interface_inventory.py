"""Runtime helpers backed by the generated interface inventory CSVs."""

from __future__ import annotations

import csv
import ipaddress
import re
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

_INTERFACE_PREFIX_VARIANTS: tuple[tuple[str, ...], ...] = (
    ("tengigabitethernet", "tengig", "te"),
    ("gigabitethernet", "gi"),
    ("fastethernet", "fa"),
    ("ethernet", "eth"),
    ("loopback", "lo"),
    ("tunnel", "tu"),
    ("port-channel", "po"),
    ("vlan", "vl"),
    ("serial", "se"),
)
_DESCRIPTION_LINK_RE = re.compile(
    r"(?:TO-|TO\s+|UPLINK\s*->\s*)([A-Z0-9._-]+)\s+([A-Z][A-Z0-9/.-]+)",
    re.IGNORECASE,
)


def _normalize_row(row: dict[str, str]) -> dict[str, str]:
    return {key: (value or "").strip() for key, value in row.items()}


def _interface_aliases(interface_name: str) -> set[str]:
    cleaned = (interface_name or "").strip().lower().replace(" ", "")
    if not cleaned:
        return set()

    aliases = {cleaned}
    for variants in _INTERFACE_PREFIX_VARIANTS:
        for variant in variants:
            if cleaned.startswith(variant):
                suffix = cleaned[len(variant):]
                aliases.update(f"{alt}{suffix}" for alt in variants)
                return aliases
    return aliases


def _interface_matches(left: str, right: str) -> bool:
    return bool(_interface_aliases(left) & _interface_aliases(right))


def _description_link_hint(description: str) -> tuple[str, str] | None:
    match = _DESCRIPTION_LINK_RE.search((description or "").strip())
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip()


def _build_link_id(local_row: dict[str, str], remote_row: dict[str, str]) -> str:
    endpoints = sorted(
        [
            f"{local_row.get('hostname', '')}:{local_row.get('interface_name', '')}",
            f"{remote_row.get('hostname', '')}:{remote_row.get('interface_name', '')}",
        ],
        key=str.lower,
    )
    return "<->".join(endpoints)


def _link_context_response(
    *,
    hostname: str,
    interface_name: str | None,
    peer_ip: str | None,
    local_row: dict[str, str] | None = None,
    remote_row: dict[str, str] | None = None,
    topology_confidence: str = "",
    resolution_method: str = "",
) -> dict[str, str]:
    if not local_row or not remote_row:
        return {
            "query_host": hostname or "",
            "query_interface": interface_name or "",
            "query_peer_ip": peer_ip or "",
            "link_id": "",
            "local_interface": local_row.get("interface_name", "") if local_row else (interface_name or ""),
            "remote_hostname": "",
            "remote_interface": "",
            "remote_mgmt_ip": "",
            "topology_confidence": "",
            "resolution_method": "",
        }

    return {
        "query_host": hostname or "",
        "query_interface": interface_name or "",
        "query_peer_ip": peer_ip or "",
        "link_id": _build_link_id(local_row, remote_row),
        "local_interface": local_row.get("interface_name", ""),
        "remote_hostname": remote_row.get("hostname", ""),
        "remote_interface": remote_row.get("interface_name", ""),
        "remote_mgmt_ip": remote_row.get("mgmt_ip", ""),
        "topology_confidence": topology_confidence,
        "resolution_method": resolution_method,
    }


def _resolve_local_row(
    *,
    host_rows: list[dict[str, str]],
    interface_name: str | None,
    peer_ip: str | None,
) -> dict[str, str] | None:
    if interface_name:
        exact_matches = [
            row for row in host_rows
            if row.get("interface_name", "").strip().lower() == interface_name.strip().lower()
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]

        alias_matches = [row for row in host_rows if _interface_matches(interface_name, row.get("interface_name", ""))]
        if len(alias_matches) == 1:
            return alias_matches[0]

    if not peer_ip:
        return None

    try:
        peer_ip_obj = ipaddress.ip_address(peer_ip)
    except ValueError:
        return None

    matches: list[dict[str, str]] = []
    for row in host_rows:
        network_cidr = row.get("network_cidr", "")
        if not network_cidr:
            continue
        try:
            network = ipaddress.ip_network(network_cidr, strict=False)
        except ValueError:
            continue
        if peer_ip_obj in network:
            matches.append(row)
    return matches[0] if len(matches) == 1 else None


def _resolve_remote_row_from_network(
    *,
    index: dict[str, Any],
    local_row: dict[str, str],
    peer_ip: str | None,
) -> dict[str, str] | None:
    network_cidr = local_row.get("network_cidr", "")
    if not network_cidr:
        return None

    remote_candidates = [
        row for row in index["by_network"].get(network_cidr, [])
        if row.get("hostname", "").strip().lower() != local_row.get("hostname", "").strip().lower()
    ]
    if not remote_candidates:
        return None

    if peer_ip:
        exact_peer = next((row for row in remote_candidates if row.get("ip_address", "") == peer_ip), None)
        if exact_peer is not None:
            return exact_peer

    hostnames = {row.get("hostname", "").strip().lower() for row in index["by_network"].get(network_cidr, []) if row.get("hostname", "")}
    if len(remote_candidates) == 1 and len(hostnames) == 2:
        return remote_candidates[0]
    return None


def _resolve_remote_row_from_description(
    *,
    index: dict[str, Any],
    local_row: dict[str, str],
) -> dict[str, str] | None:
    hint = _description_link_hint(local_row.get("description", ""))
    if hint is None:
        return None

    remote_hostname, remote_interface_hint = hint
    host_rows = index["by_hostname"].get(remote_hostname.lower(), [])
    matches = [row for row in host_rows if _interface_matches(remote_interface_hint, row.get("interface_name", ""))]
    return matches[0] if len(matches) == 1 else None


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


def resolve_link_context(
    hostname: str,
    interface_name: str | None = None,
    *,
    peer_ip: str | None = None,
    full: bool = False,
) -> dict[str, str]:
    """Resolve a device/interface or device/peer-IP to a deterministic inter-device link."""
    search = (hostname or "").strip().lower()
    if not search:
        return _link_context_response(hostname="", interface_name=interface_name, peer_ip=peer_ip)

    _rows, index = _load_interface_rows(full=full)
    host_rows = index["by_hostname"].get(search, [])
    if not host_rows:
        return _link_context_response(hostname=hostname, interface_name=interface_name, peer_ip=peer_ip)

    local_row = _resolve_local_row(host_rows=host_rows, interface_name=interface_name, peer_ip=peer_ip)
    if local_row is None:
        return _link_context_response(hostname=hostname, interface_name=interface_name, peer_ip=peer_ip)

    remote_row = _resolve_remote_row_from_network(index=index, local_row=local_row, peer_ip=peer_ip)
    if remote_row is not None:
        return _link_context_response(
            hostname=hostname,
            interface_name=interface_name,
            peer_ip=peer_ip,
            local_row=local_row,
            remote_row=remote_row,
            topology_confidence="high",
            resolution_method="network_cidr",
        )

    remote_row = _resolve_remote_row_from_description(index=index, local_row=local_row)
    if remote_row is not None:
        return _link_context_response(
            hostname=hostname,
            interface_name=interface_name,
            peer_ip=peer_ip,
            local_row=local_row,
            remote_row=remote_row,
            topology_confidence="high",
            resolution_method="description",
        )

    return _link_context_response(
        hostname=hostname,
        interface_name=local_row.get("interface_name") or interface_name,
        peer_ip=peer_ip,
        local_row=local_row,
    )
