#!/usr/bin/env python3
"""Collect interface inventories from devices and write lean/full CSV exports."""

from __future__ import annotations

import csv
import ipaddress
import re
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.formatters import is_error, parse_output
from src.tools.ssh_executor import execute_cli

INVENTORY_PATH = BACKEND_ROOT / "inventory" / "inventory.csv"
OUTPUT_PATH = BACKEND_ROOT / "inventory" / "interfaces.csv"
FULL_OUTPUT_PATH = BACKEND_ROOT / "inventory" / "interfaces_full.csv"

SHOW_IP_INTERFACE_BRIEF = "show ip interface brief"
SHOW_INTERFACE_SECTION = "show running-config | section ^interface"
SHOW_VLAN_BRIEF = "show vlan brief"
SHOW_INTERFACES_TRUNK = "show interfaces trunk"
INTERFACE_LINE_RE = re.compile(r"^interface\s+(?P<name>\S+)\s*$", re.IGNORECASE)
DESCRIPTION_RE = re.compile(r"^\s*description\s+(?P<text>.+?)\s*$", re.IGNORECASE)
SWITCHPORT_MODE_RE = re.compile(r"^\s*switchport mode\s+(?P<mode>\S+)\s*$", re.IGNORECASE)
ACCESS_VLAN_RE = re.compile(r"^\s*switchport access vlan\s+(?P<vlan>\d+)\s*$", re.IGNORECASE)
TRUNK_NATIVE_VLAN_RE = re.compile(r"^\s*switchport trunk native vlan\s+(?P<vlan>\d+)\s*$", re.IGNORECASE)
ENCAP_DOT1Q_RE = re.compile(r"^\s*encapsulation dot1Q\s+(?P<vlan>\d+)(?:\s+native)?\s*$", re.IGNORECASE)
IP_ADDRESS_RE = re.compile(
    r"^\s*ip address\s+(?P<ip>\S+)\s+(?P<mask>\S+)(?:\s+(?P<secondary>secondary))?\s*$",
    re.IGNORECASE,
)

CSV_FIELDS = [
    "hostname",
    "mgmt_ip",
    "site",
    "device_role",
    "os_platform",
    "interface_name",
    "parent_interface",
    "description",
    "interface_mode",
    "access_vlan",
    "native_vlan",
    "vlan_tag",
    "ip_address",
    "netmask",
    "prefix_length",
    "cidr",
    "network",
    "network_cidr",
]


def _load_inventory() -> list[dict[str, str]]:
    with open(INVENTORY_PATH, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _empty_interface_data() -> dict[str, object]:
    return {
        "parent_interface": "",
        "description": "",
        "interface_mode": "",
        "access_vlan": "",
        "native_vlan": "",
        "vlan_tag": "",
        "addresses": [],
    }


def _normalize_interface_name(name: str) -> str:
    value = (name or "").strip()
    mapping = (
        ("GigabitEthernet", "GigabitEthernet"),
        ("FastEthernet", "FastEthernet"),
        ("TenGigabitEthernet", "TenGigabitEthernet"),
        ("Port-channel", "Port-channel"),
        ("Loopback", "Loopback"),
        ("Tunnel", "Tunnel"),
        ("Vlan", "Vlan"),
        ("Gi", "GigabitEthernet"),
        ("Fa", "FastEthernet"),
        ("Te", "TenGigabitEthernet"),
        ("Po", "Port-channel"),
        ("Lo", "Loopback"),
        ("Tu", "Tunnel"),
        ("Vl", "Vlan"),
    )
    for short, full in mapping:
        if value.startswith(short):
            return full + value[len(short):]
    return value


def _parent_interface_name(interface_name: str) -> str:
    value = (interface_name or "").strip()
    if "." not in value:
        return ""
    return value.split(".", 1)[0]


def _parse_brief_interfaces(body: str) -> list[dict[str, str]]:
    interfaces: list[dict[str, str]] = []
    lines = [line.rstrip("\n") for line in body.splitlines() if line.strip()]
    if not lines:
        return interfaces

    header_index = next(
        (idx for idx, line in enumerate(lines) if "IP-Address" in line and "Protocol" in line),
        None,
    )
    if header_index is None:
        return interfaces

    header = lines[header_index]
    ip_start = header.index("IP-Address")
    ok_start = header.index("OK?")
    status_start = header.index("Status")
    protocol_start = header.index("Protocol")

    for line in lines[header_index + 1:]:
        interface_name = line[:ip_start].strip()
        if not interface_name:
            continue
        ip_address = line[ip_start:ok_start].strip()
        status = line[status_start:protocol_start].strip()
        protocol = line[protocol_start:].strip()
        interfaces.append({
            "interface_name": interface_name,
            "brief_ip": "" if ip_address.lower() == "unassigned" else ip_address,
            "status": status,
            "protocol": protocol,
        })

    return interfaces


def _parse_config(body: str) -> dict[str, dict[str, object]]:
    interface_map: dict[str, dict[str, object]] = {}
    current_interface = ""
    for line in body.splitlines():
        interface_match = INTERFACE_LINE_RE.match(line.strip())
        if interface_match:
            current_interface = interface_match.group("name")
            interface_map.setdefault(current_interface, _empty_interface_data())
            continue

        if not current_interface:
            continue

        description_match = DESCRIPTION_RE.match(line)
        if description_match:
            interface_map.setdefault(current_interface, _empty_interface_data())
            interface_map[current_interface]["description"] = description_match.group("text").strip()
            continue

        switchport_mode_match = SWITCHPORT_MODE_RE.match(line)
        if switchport_mode_match:
            interface_map.setdefault(current_interface, _empty_interface_data())
            interface_map[current_interface]["interface_mode"] = switchport_mode_match.group("mode").strip().lower()
            continue

        access_vlan_match = ACCESS_VLAN_RE.match(line)
        if access_vlan_match:
            interface_map.setdefault(current_interface, _empty_interface_data())
            interface_map[current_interface]["access_vlan"] = access_vlan_match.group("vlan").strip()
            if not interface_map[current_interface]["interface_mode"]:
                interface_map[current_interface]["interface_mode"] = "access"
            continue

        trunk_native_match = TRUNK_NATIVE_VLAN_RE.match(line)
        if trunk_native_match:
            interface_map.setdefault(current_interface, _empty_interface_data())
            interface_map[current_interface]["native_vlan"] = trunk_native_match.group("vlan").strip()
            if not interface_map[current_interface]["interface_mode"]:
                interface_map[current_interface]["interface_mode"] = "trunk"
            continue

        encapsulation_match = ENCAP_DOT1Q_RE.match(line)
        if encapsulation_match:
            interface_map.setdefault(current_interface, _empty_interface_data())
            interface_map[current_interface]["vlan_tag"] = encapsulation_match.group("vlan").strip()
            if not interface_map[current_interface]["interface_mode"]:
                interface_map[current_interface]["interface_mode"] = "subinterface"
            continue

        ip_match = IP_ADDRESS_RE.match(line)
        if not ip_match:
            continue

        ip_address = ip_match.group("ip")
        netmask = ip_match.group("mask")
        iface = ipaddress.IPv4Interface(f"{ip_address}/{netmask}")
        network = iface.network
        interface_map.setdefault(current_interface, _empty_interface_data())
        addresses = interface_map[current_interface]["addresses"]
        assert isinstance(addresses, list)
        addresses.append({
            "ip_address": ip_address,
            "netmask": netmask,
            "prefix_length": str(network.prefixlen),
            "cidr": f"{ip_address}/{network.prefixlen}",
            "network": str(network.network_address),
            "network_cidr": str(network),
        })

    for interface_name, data in interface_map.items():
        name_lower = interface_name.lower()
        if name_lower.startswith("vlan"):
            data["interface_mode"] = "svi"
            if not data.get("vlan_tag"):
                data["vlan_tag"] = interface_name[4:]
        elif "." in interface_name and data.get("vlan_tag"):
            data["interface_mode"] = "subinterface"
            data["parent_interface"] = _parent_interface_name(interface_name)
        elif name_lower.startswith("loopback") and not data.get("interface_mode"):
            data["interface_mode"] = "loopback"
        elif name_lower.startswith("tunnel") and not data.get("interface_mode"):
            data["interface_mode"] = "tunnel"
        elif data.get("addresses") and not data.get("interface_mode"):
            data["interface_mode"] = "routed"

    for interface_name, data in list(interface_map.items()):
        parent_interface = str(data.get("parent_interface", "") or "")
        if not parent_interface:
            continue
        parent_data = interface_map.setdefault(parent_interface, _empty_interface_data())
        parent_data["interface_mode"] = "subinterface_parent"

    return interface_map


def _build_row(
    device: dict[str, str],
    interface_name: str,
    parent_interface: str = "",
    description: str = "",
    interface_mode: str = "",
    access_vlan: str = "",
    native_vlan: str = "",
    vlan_tag: str = "",
    address_data: dict[str, str] | None = None,
) -> dict[str, str]:
    address_data = address_data or {}
    return {
        "hostname": device["hostname"],
        "mgmt_ip": device["ip_address"],
        "site": device["site"],
        "device_role": device["device_role"],
        "os_platform": device["os_platform"],
        "interface_name": interface_name,
        "parent_interface": parent_interface,
        "description": description,
        "interface_mode": interface_mode,
        "access_vlan": access_vlan,
        "native_vlan": native_vlan,
        "vlan_tag": vlan_tag,
        "ip_address": address_data.get("ip_address", ""),
        "netmask": address_data.get("netmask", ""),
        "prefix_length": address_data.get("prefix_length", ""),
        "cidr": address_data.get("cidr", ""),
        "network": address_data.get("network", ""),
        "network_cidr": address_data.get("network_cidr", ""),
    }


def _merge_interface_rows(
    device: dict[str, str],
    brief_interfaces: list[dict[str, str]],
    config_map: dict[str, dict[str, object]],
) -> tuple[list[dict[str, str]], dict[str, dict[str, str]]]:
    rows: list[dict[str, str]] = []
    brief_meta: dict[str, dict[str, str]] = {}
    seen_interfaces: set[str] = set()

    for item in brief_interfaces:
        interface_name = item["interface_name"]
        brief_meta[interface_name] = item
        seen_interfaces.add(interface_name)
        config_item = config_map.get(interface_name, {})
        parent_interface = ""
        description = ""
        interface_mode = ""
        access_vlan = ""
        native_vlan = ""
        vlan_tag = ""
        addresses: list[dict[str, str]] = []
        if isinstance(config_item, dict):
            parent_interface = str(config_item.get("parent_interface", "") or "")
            description = str(config_item.get("description", "") or "")
            interface_mode = str(config_item.get("interface_mode", "") or "")
            access_vlan = str(config_item.get("access_vlan", "") or "")
            native_vlan = str(config_item.get("native_vlan", "") or "")
            vlan_tag = str(config_item.get("vlan_tag", "") or "")
            maybe_addresses = config_item.get("addresses", [])
            if isinstance(maybe_addresses, list):
                addresses = maybe_addresses
        if addresses:
            for address_data in addresses:
                rows.append(
                    _build_row(
                        device,
                        interface_name,
                        parent_interface,
                        description,
                        interface_mode,
                        access_vlan,
                        native_vlan,
                        vlan_tag,
                        address_data,
                    )
                )
            continue

        fallback_ip = item.get("brief_ip", "")
        rows.append(
            _build_row(
                device,
                interface_name,
                parent_interface,
                description,
                interface_mode,
                access_vlan,
                native_vlan,
                vlan_tag,
                {"ip_address": fallback_ip} if fallback_ip else None,
            )
        )

    for interface_name in sorted(config_map):
        if interface_name in seen_interfaces:
            continue
        config_item = config_map[interface_name]
        parent_interface = str(config_item.get("parent_interface", "") or "")
        description = str(config_item.get("description", "") or "")
        interface_mode = str(config_item.get("interface_mode", "") or "")
        access_vlan = str(config_item.get("access_vlan", "") or "")
        native_vlan = str(config_item.get("native_vlan", "") or "")
        vlan_tag = str(config_item.get("vlan_tag", "") or "")
        maybe_addresses = config_item.get("addresses", [])
        addresses = maybe_addresses if isinstance(maybe_addresses, list) else []
        if not addresses:
            rows.append(
                _build_row(
                    device,
                    interface_name,
                    parent_interface,
                    description,
                    interface_mode,
                    access_vlan,
                    native_vlan,
                    vlan_tag,
                )
            )
            continue
        for address_data in addresses:
            rows.append(
                _build_row(
                    device,
                    interface_name,
                    parent_interface,
                    description,
                    interface_mode,
                    access_vlan,
                    native_vlan,
                    vlan_tag,
                    address_data,
                )
            )

    return rows, brief_meta


def _parse_vlan_brief(body: str) -> dict[str, str]:
    access_map: dict[str, str] = {}
    started = False
    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("VLAN Name"):
            started = True
            continue
        if not started or stripped.startswith("----"):
            continue
        parts = re.split(r"\s{2,}", stripped)
        if len(parts) < 3:
            continue
        vlan_id = parts[0]
        if not vlan_id.isdigit():
            continue
        ports_field = parts[3] if len(parts) > 3 else ""
        if not ports_field:
            continue
        for port in [item.strip() for item in ports_field.split(",") if item.strip()]:
            access_map[_normalize_interface_name(port)] = vlan_id
    return access_map


def _parse_trunk_info(body: str) -> dict[str, dict[str, str]]:
    trunk_map: dict[str, dict[str, str]] = {}
    current_section = ""
    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("Port") and "Native vlan" in stripped:
            current_section = "native"
            continue
        if stripped.startswith("Port") and "Vlans allowed on trunk" in stripped:
            current_section = "allowed"
            continue
        if stripped.startswith("Port") and "Vlans allowed and active" in stripped:
            current_section = "active"
            continue
        if stripped.startswith("Port") and "Vlans in spanning tree" in stripped:
            current_section = "stp"
            continue
        if current_section != "native" or stripped.startswith("-"):
            continue
        parts = stripped.split()
        if len(parts) < 5:
            continue
        interface_name = _normalize_interface_name(parts[0])
        trunk_map[interface_name] = {
            "interface_mode": "trunk",
            "native_vlan": parts[4],
        }
    return trunk_map


def _overlay_optional_l2_data(
    config_map: dict[str, dict[str, object]],
    access_vlan_map: dict[str, str],
    trunk_map: dict[str, dict[str, str]],
) -> dict[str, dict[str, object]]:
    for interface_name, vlan_id in access_vlan_map.items():
        config_map.setdefault(interface_name, _empty_interface_data())
        if not config_map[interface_name].get("access_vlan"):
            config_map[interface_name]["access_vlan"] = vlan_id
        if not config_map[interface_name].get("interface_mode"):
            config_map[interface_name]["interface_mode"] = "access"

    for interface_name, trunk_data in trunk_map.items():
        config_map.setdefault(interface_name, _empty_interface_data())
        config_map[interface_name]["interface_mode"] = trunk_data.get("interface_mode", "trunk")
        if trunk_data.get("native_vlan"):
            config_map[interface_name]["native_vlan"] = trunk_data["native_vlan"]

    return config_map


def _has_child_subinterfaces(interface_name: str, all_names: set[str]) -> bool:
    prefix = interface_name + "."
    return any(name.startswith(prefix) for name in all_names)


def _is_relevant_row(
    row: dict[str, str],
    brief_meta: dict[str, dict[str, str]],
    all_names: set[str],
) -> bool:
    if row["ip_address"]:
        return True

    description = row["description"].strip().lower()
    if description and not description.startswith("unused"):
        return True

    if row["access_vlan"] or row["native_vlan"] or row["vlan_tag"]:
        return True

    interface_name = row["interface_name"]
    if _has_child_subinterfaces(interface_name, all_names):
        return True

    if interface_name.lower().startswith("loopback"):
        return False

    return row["interface_mode"] in {"trunk", "access", "subinterface", "svi", "tunnel", "routed"}


def _collect_device(device: dict[str, str]) -> tuple[list[dict[str, str]], list[dict[str, str]], list[str]]:
    host = device["hostname"]
    device_ip = device["ip_address"]
    os_platform = device["os_platform"]
    errors: list[str] = []

    raw_brief = execute_cli(device_ip, os_platform, SHOW_IP_INTERFACE_BRIEF)
    _brief_host, _brief_ip, _brief_os, brief_body = parse_output(raw_brief)
    if is_error(brief_body or raw_brief):
        errors.append(
            f"{host}: failed `{SHOW_IP_INTERFACE_BRIEF}` -> "
            f"{(brief_body or raw_brief).splitlines()[0].strip()}"
        )
        return [], [], errors

    raw_config = execute_cli(device_ip, os_platform, SHOW_INTERFACE_SECTION)
    _cfg_host, _cfg_ip, _cfg_os, config_body = parse_output(raw_config)
    if is_error(config_body or raw_config):
        errors.append(
            f"{host}: failed `{SHOW_INTERFACE_SECTION}` -> "
            f"{(config_body or raw_config).splitlines()[0].strip()}"
        )
        return [], [], errors

    brief_interfaces = _parse_brief_interfaces(brief_body)
    config_map = _parse_config(config_body)
    optional_vlan_output = execute_cli(device_ip, os_platform, SHOW_VLAN_BRIEF)
    _vh, _vip, _vos, vlan_body = parse_output(optional_vlan_output)
    access_vlan_map = {} if is_error(vlan_body or optional_vlan_output) else _parse_vlan_brief(vlan_body)
    optional_trunk_output = execute_cli(device_ip, os_platform, SHOW_INTERFACES_TRUNK)
    _th, _tip, _tos, trunk_body = parse_output(optional_trunk_output)
    trunk_map = {} if is_error(trunk_body or optional_trunk_output) else _parse_trunk_info(trunk_body)
    config_map = _overlay_optional_l2_data(config_map, access_vlan_map, trunk_map)
    full_rows, brief_meta = _merge_interface_rows(device, brief_interfaces, config_map)
    if not full_rows:
        errors.append(f"{host}: no interface inventory rows were parsed from device output")
        return [], [], errors

    interface_names = {row["interface_name"] for row in full_rows}
    lean_rows = [
        row for row in full_rows
        if _is_relevant_row(row, brief_meta, interface_names)
    ]
    return lean_rows, full_rows, errors


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    inventory = _load_inventory()
    lean_rows: list[dict[str, str]] = []
    full_rows: list[dict[str, str]] = []
    errors: list[str] = []

    for device in inventory:
        device_lean_rows, device_full_rows, device_errors = _collect_device(device)
        lean_rows.extend(device_lean_rows)
        full_rows.extend(device_full_rows)
        errors.extend(device_errors)

    lean_rows.sort(key=lambda row: (row["hostname"], row["interface_name"], row["ip_address"]))
    full_rows.sort(key=lambda row: (row["hostname"], row["interface_name"], row["ip_address"]))

    _write_csv(OUTPUT_PATH, lean_rows)
    _write_csv(FULL_OUTPUT_PATH, full_rows)

    print(f"Wrote {len(lean_rows)} lean interface rows to {OUTPUT_PATH}")
    print(f"Wrote {len(full_rows)} full interface rows to {FULL_OUTPUT_PATH}")
    if errors:
        print("Collection notes:", file=sys.stderr)
        for entry in errors:
            print(f"- {entry}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
