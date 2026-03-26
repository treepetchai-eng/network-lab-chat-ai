"""Advisory Impact Checker — two-phase LLM+SSH pipeline.

Phase 1 (Plan)  : Fetch full advisory page → LLM reads it → decides commands.
Phase 2 (Assess): Run commands via SSH → LLM compares output vs advisory → verdict.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
from html.parser import HTMLParser
from typing import Any, Callable

import requests

logger = logging.getLogger(__name__)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
_MAX_COMMANDS = int(os.getenv("ADVISORY_CHECK_MAX_COMMANDS", "5"))

# ── Caches ────────────────────────────────────────────────────────────────────
_page_cache: dict[str, str] = {}
_page_cache_lock = threading.Lock()

_WORKAROUND_SECTION_RE = re.compile(
    r"(?:^|\n)\s*Workarounds?\s*\n(.*?)(?=\n\s*(?:Fixed Software|Recommendations|Exploitation|Source|Cisco Bug|Details|Affected Products|References|Legal Disclaimer|\Z))",
    re.DOTALL | re.IGNORECASE,
)

# ── Command normalization ─────────────────────────────────────────────────────
_CMD_REPLACEMENTS = [
    ("running-configuration", "running-config"),
    ("startup-configuration", "startup-config"),
    ("show ip ospf neighbour", "show ip ospf neighbor"),
    ("show ip eigrp neighbour", "show ip eigrp neighbor"),
]

# ── Platform labels ───────────────────────────────────────────────────────────
_DEVICE_HEADER_RE = re.compile(r"^\[Device:.*?\]\s*$", re.MULTILINE)


def _cli_output_is_empty(output: str) -> bool:
    """Return True if CLI output contains only the [Device:...] header and whitespace."""
    stripped = _DEVICE_HEADER_RE.sub("", output).strip()
    return stripped == "" or stripped.startswith("[SSH ERROR]")


def _is_show_run_command(cmd: str) -> bool:
    """Return True if command is a 'show running-config' variant (section/include)."""
    c = cmd.strip().lower()
    return c.startswith("show running-config") or c.startswith("show run ")


def _cli_output_body(output: str) -> str:
    """Return CLI output without the [Device:...] header line."""
    return _DEVICE_HEADER_RE.sub("", output).strip()


# ── Programmatic evidence checks (override LLM when evidence is clear) ────

_PORT_ADVISORY_RE = re.compile(r"(?:port|udp|tcp)\s+(\d{2,5})", re.IGNORECASE)


def _check_port_listening(commands_run: list[dict], advisory_text: str, title: str) -> dict | None:
    """If advisory mentions a specific port and show udp/tcp confirms it's listening → affected."""
    # Extract port numbers from advisory text + title
    target_ports: set[str] = set()
    for source in (title, advisory_text):
        for m in _PORT_ADVISORY_RE.finditer(source):
            port = m.group(1)
            if 1024 <= int(port) <= 65535:  # skip well-known ports that are too generic
                target_ports.add(port)

    if not target_ports:
        return None

    for cr in commands_run:
        cmd_lower = cr["command"].strip().lower()
        if not (cmd_lower.startswith("show udp") or cmd_lower.startswith("show tcp")):
            continue
        body = _cli_output_body(cr["output"])
        if not body:
            continue
        for port in target_ports:
            # Match port in the Local Port column of show udp/tcp output
            if re.search(rf"\b{port}\b", body):
                return {
                    "verdict": "affected",
                    "confidence": 0.95,
                    "explanation": (
                        f"Port {port} is actively listening on this device "
                        f"(confirmed by '{cr['command']}'). "
                        f"The advisory identifies this port as the attack vector."
                    ),
                }
    return None


    # NOTE: We intentionally do NOT have a generic "show run has output → affected"
    # override here. show running-config output requires context-sensitive analysis
    # (e.g. SNMP section exists but advisory only targets SNMPv3, not v2c).
    # Only _check_port_listening provides hard programmatic evidence.
    # All other cases are deferred to LLM assessment.


# ── Keyword → fallback commands (when LLM picks only 'show version') ──────
_FEATURE_KEYWORD_COMMANDS: list[tuple[list[str], list[str]]] = [
    (["qos", "quality of service", "port 18999"],
     ["show running-config | section policy-map", "show udp"]),
    (["http", "web services", "web server", "web ui", "web-based"],
     ["show running-config | include ip http"]),
    (["vpn", "anyconnect", "webvpn", "ssl vpn", "remote access"],
     ["show running-config | section webvpn", "show running-config | section crypto"]),
    (["smart install"],
     ["show vstack config"]),
    (["snmp"],
     ["show running-config | section snmp"]),
    (["sip", "voice"],
     ["show running-config | section voice"]),
    (["dhcp"],
     ["show running-config | section ip dhcp"]),
    (["bgp"],
     ["show running-config | section router bgp"]),
    (["ospf"],
     ["show running-config | section router ospf"]),
    (["eigrp"],
     ["show running-config | section router eigrp"]),
    (["nat"],
     ["show running-config | include ip nat"]),
    (["multicast", "igmp", "pim"],
     ["show running-config | include ip multicast"]),
    (["mpls"],
     ["show running-config | include mpls"]),
    (["zone", "zbfw", "zone-based firewall"],
     ["show running-config | section zone"]),
    (["ike", "ipsec", "isakmp", "ikev2"],
     ["show running-config | section crypto isakmp", "show running-config | section crypto ikev2"]),
    (["aaa", "tacacs", "radius"],
     ["show running-config | section aaa"]),
    (["ntp"],
     ["show running-config | include ntp"]),
    (["dns", "name-server"],
     ["show running-config | include ip name-server"]),
    (["ssh"],
     ["show ip ssh"]),
]


def _feature_commands_from_title(title: str) -> list[str]:
    """Derive check commands from advisory title keywords when LLM fails to pick them."""
    title_lower = title.lower()
    for keywords, cmds in _FEATURE_KEYWORD_COMMANDS:
        if any(kw in title_lower for kw in keywords):
            return list(cmds)
    return []


_PLATFORM_LABEL = {
    "cisco_ios":    "Cisco IOS",
    "cisco_ios_xe": "Cisco IOS XE",
    "cisco_ios_xr": "Cisco IOS XR",
    "cisco_asa":    "Cisco ASA",
    "cisco_ftd":    "Cisco FTD",
    "cisco_nxos":   "Cisco NX-OS",
}

# ── Feature-to-command reference table ────────────────────────────────────────
_FEATURE_COMMAND_TABLE = """
| Feature / Service          | IOS Commands to check                                  |
|----------------------------|--------------------------------------------------------|
| HTTP / Web Services        | show running-config | include ip http                  |
|                            | show running-config | section webvpn                   |
| VPN / AnyConnect / WebVPN  | show running-config | section webvpn                   |
|                            | show running-config | section crypto                   |
| Smart Install              | show vstack config                                     |
| SNMP                       | show running-config | section snmp                     |
|                            | show snmp user                                         |
| SSH                        | show ip ssh                                            |
| SIP / Voice                | show running-config | section voice                    |
| DHCP                       | show running-config | section ip dhcp                  |
| BGP                        | show running-config | section router bgp               |
| OSPF                       | show running-config | section router ospf              |
| EIGRP                      | show running-config | section router eigrp             |
| QoS                        | show running-config | section policy-map               |
|                            | show tcp brief | include 18999                         |
| NAT                        | show running-config | include ip nat                   |
| ACL                        | show running-config | section access-list              |
| Multicast                  | show running-config | include ip multicast             |
| MPLS                       | show running-config | include mpls                     |
| Zone-Based Firewall        | show running-config | section zone                     |
| IKE / IPsec               | show running-config | section crypto isakmp            |
|                            | show running-config | section crypto ikev2             |
| AAA / TACACS / RADIUS      | show running-config | section aaa                     |
| NTP                        | show running-config | include ntp                      |
| DNS                        | show running-config | include ip name-server           |
""".strip()


class _TextExtractor(HTMLParser):
    """Extract readable text from Cisco advisory HTML page."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style", "nav", "footer", "header"):
            self._skip = True
        if tag in ("p", "li", "br", "h1", "h2", "h3", "h4", "tr", "div"):
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "nav", "footer", "header"):
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _fetch_advisory_page(url: str) -> str:
    """Fetch full advisory page text from Cisco, with caching."""
    if not url:
        return ""

    with _page_cache_lock:
        if url in _page_cache:
            return _page_cache[url]

    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "AIOps-Advisory-Checker/1.0",
            "Accept": "text/html",
        })
        if resp.status_code != 200:
            logger.warning("Advisory page fetch failed: %s → %d", url, resp.status_code)
            return ""

        parser = _TextExtractor()
        parser.feed(resp.text)
        text = parser.get_text()

        with _page_cache_lock:
            _page_cache[url] = text

        return text
    except Exception as exc:
        logger.warning("Advisory page fetch error for %s: %s", url, exc)
        return ""


def _strip_think(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


def _safe_json(text: str, fallback: dict) -> dict:
    clean = _strip_think(text)
    match = _JSON_BLOCK_RE.search(clean)
    if not match:
        return fallback
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return fallback


def _llm_enabled() -> bool:
    if os.getenv("AIOPS_DISABLE_LLM", "").strip() == "1":
        return False
    if "pytest" in sys.modules or os.getenv("PYTEST_CURRENT_TEST"):
        return False
    return True


def _normalize_ios_command(cmd: str) -> str:
    """Fix common Cisco IOS command syntax errors generated by LLM."""
    normalized = cmd.strip()
    for wrong, correct in _CMD_REPLACEMENTS:
        normalized = normalized.replace(wrong, correct)
    return normalized


def clear_plan_cache(advisory_id: str | None = None) -> int:
    """Clear advisory page cache. Pass advisory_id url to clear one, or None for all."""
    with _page_cache_lock:
        if advisory_id:
            # page cache is keyed by URL, just clear all for simplicity
            removed = len(_page_cache)
            _page_cache.clear()
        else:
            removed = len(_page_cache)
            _page_cache.clear()
    return removed


# ── Main entry point ──────────────────────────────────────────────────────────

def check_advisory_impact(
    *,
    device: dict[str, Any],
    advisory: dict[str, Any],
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Two-phase advisory impact check: LLM plans commands → SSH → LLM assesses."""
    commands_run: list[dict[str, str]] = []
    # Track workaround + feature for persistence
    _wa: dict[str, Any] = {"has_workaround": None, "workaround_text": ""}
    _feature = ""

    def _emit(event_type: str, data: dict[str, Any]) -> None:
        if on_event:
            on_event({"type": event_type, **data})

    def _result(verdict: str, confidence: float, explanation: str) -> dict[str, Any]:
        return {
            "verdict": verdict,
            "confidence": confidence,
            "explanation": explanation,
            "commands_run": commands_run,
            "has_workaround": _wa["has_workaround"],
            "workaround_text": _wa["workaround_text"],
            "feature_checked": _feature,
        }

    fallback = _result("uncertain", 0.0, "Check could not complete — LLM or SSH not available.")

    if not _llm_enabled():
        return fallback

    hostname    = device.get("hostname", "")
    ip_address  = device.get("ip_address", "")
    os_platform = device.get("os_platform", "cisco_ios")

    if not ip_address:
        fallback["explanation"] = f"Device {hostname} has no IP address configured."
        return fallback

    try:
        from langchain_core.messages import HumanMessage
        from src.llm_factory import create_chat_model
        from src.tools.cli_tool import create_run_cli_tool

        device_cache = {
            hostname: {
                "ip_address": ip_address,
                "os_platform": os_platform,
                "device_role": device.get("device_role", ""),
                "site": device.get("site", ""),
                "version": device.get("version", ""),
            }
        }
        run_cli = create_run_cli_tool(device_cache)

        advisory_id = advisory.get("advisory_id", "")
        title       = advisory.get("title", "")
        sir         = advisory.get("sir", "")
        cvss_score  = advisory.get("cvss_score", 0)
        cves        = ", ".join(advisory.get("cves", [])[:5]) or advisory_id
        summary     = advisory.get("summary") or ""
        workaround  = advisory.get("workaround") or ""

        platform_label = _PLATFORM_LABEL.get(os_platform, os_platform)

        # ══════════════════════════════════════════════════════════════════════
        # PHASE 1: Fetch advisory → LLM reads full text → plans commands
        # ══════════════════════════════════════════════════════════════════════

        _emit("status", {"message": f"Reading advisory: {title[:60]}..."})

        pub_url   = advisory.get("publication_url") or ""
        full_page = _fetch_advisory_page(pub_url) if pub_url else ""

        if full_page:
            advisory_text = full_page
            _emit("status", {"message": "Analyzing advisory..."})

            # Extract workaround for UI
            wa_match = _WORKAROUND_SECTION_RE.search(full_page)
            wa_text = wa_match.group(1).strip() if wa_match else ""
            if not wa_text:
                has_workaround = not bool(re.search(r"(?i)there (?:are|is) no workaround", full_page))
                if not has_workaround:
                    wa_text = "No workaround is available. Upgrade to a fixed software release."
            else:
                has_workaround = "no workaround" not in wa_text.lower()[:120]
            _wa.update(has_workaround=has_workaround, workaround_text=wa_text)
            _emit("workaround", {"has_workaround": has_workaround, "workaround_text": wa_text})
        else:
            advisory_text = (
                f"Title: {title}\nSummary: {summary}\n"
                f"Workaround: {workaround}"
            )
            _emit("status", {"message": "Page fetch failed, using API summary"})
            if workaround:
                no_wa = "no workaround" in workaround.lower()[:120]
                _wa.update(has_workaround=not no_wa, workaround_text=workaround)
                _emit("workaround", {"has_workaround": not no_wa, "workaround_text": workaround})
            else:
                _wa.update(has_workaround=False, workaround_text="Workaround status unknown.")
                _emit("workaround", {"has_workaround": False, "workaround_text": "Workaround status unknown."})

        # ── LLM Plan: read advisory → decide commands ─────────────────────────

        plan_prompt = (
            f"You are a Cisco network security engineer.\n"
            f"You must determine what CLI commands to run on a device to check if it is affected by a vulnerability.\n\n"
            f"=== TARGET DEVICE ===\n"
            f"Hostname : {hostname}\n"
            f"Platform : {platform_label}\n"
            f"Version  : {device.get('version', 'unknown')}\n\n"
            f"=== ADVISORY ===\n"
            f"ID       : {advisory_id}\n"
            f"Severity : [{sir}] CVSS {cvss_score}\n"
            f"Title    : {title}\n"
            f"CVEs     : {cves}\n\n"
            f"=== FULL ADVISORY TEXT ===\n{advisory_text}\n\n"
            f"=== FEATURE-TO-COMMAND REFERENCE (for {platform_label}) ===\n"
            f"{_FEATURE_COMMAND_TABLE}\n\n"
            f"=== INSTRUCTIONS ===\n"
            f"Read the advisory above carefully. Then return a JSON plan.\n\n"
            f"1. First, identify what the advisory is about:\n"
            f"   - Does it affect SPECIFIC HARDWARE MODELS ONLY? (e.g. 'Industrial Routers IR809/829', 'CGR 1000')\n"
            f"     → Return: {{\"commands\":[\"show version\"],\"reason\":\"hardware model check\"}}\n\n"
            f"   - Does it affect a FEATURE that must be configured?\n"
            f"     → Find the correct commands using the REFERENCE TABLE above or the advisory's own recommendations.\n"
            f"     → Return: {{\"commands\":[\"cmd1\",\"cmd2\"],\"reason\":\"checking if <feature> is configured\"}}\n\n"
            f"   - Is it a CORE vulnerability that affects ALL devices with this software version?\n"
            f"     (Cannot be disabled by configuration. No feature to check.)\n"
            f"     → Return: {{\"commands\":[],\"verdict\":\"affected\",\"reason\":\"core vulnerability, always active\"}}\n\n"
            f"2. RULES:\n"
            f"   - If the advisory mentions specific 'show' commands for verification, USE THOSE EXACT COMMANDS.\n"
            f"   - Otherwise, use the REFERENCE TABLE to pick the right commands for the feature.\n"
            f"   - Only use commands valid for {platform_label}.\n"
            f"   - 'feature <name>' is NX-OS syntax — NEVER use on IOS.\n"
            f"   - Do NOT use 'show version' unless checking hardware models.\n"
            f"   - Do NOT invent feature names. Use real IOS keywords from the reference table.\n"
            f"   - Do NOT use broad patterns like 'include secure' or 'include firewall' — use specific feature keywords.\n"
            f"   - Max {_MAX_COMMANDS} commands.\n\n"
            f"Return ONLY valid JSON."
        )

        _emit("status", {"message": "Planning check commands..."})

        llm_plan  = create_chat_model(reasoning=True)
        reply     = llm_plan.invoke([HumanMessage(content=plan_prompt)])
        plan_text = _strip_think(str(getattr(reply, "content", "") or ""))
        plan      = _safe_json(plan_text, {})

        logger.info("Advisory plan: %s → cmds=%s reason=%s raw=%s",
                    advisory_id, plan.get("commands"), plan.get("reason"), plan_text[:300])

        commands = plan.get("commands") or []
        feature  = plan.get("reason") or title
        _feature = feature

        _emit("plan", {"feature": feature, "commands": commands})

        # ── Direct verdict (core/always-on) ───────────────────────────────────

        if not commands and plan.get("verdict") == "affected":
            explanation = str(plan.get("reason") or "Core vulnerability — all devices running this version are affected.")
            _emit("verdict", {"verdict": "affected", "confidence": 0.90, "explanation": explanation})
            return _result("affected", 0.90, explanation)

        # ── Fallback if no commands returned ──────────────────────────────────

        if not commands:
            logger.warning("No commands returned for %s, using show version fallback", advisory_id)
            commands = ["show version"]

        # ── Guard: override 'show version' when advisory is about a feature ──
        #    LLM sometimes picks 'show version' for feature-based advisories.
        only_show_version = all(
            c.strip().lower().startswith("show version") for c in commands
        )
        reason_lower = (plan.get("reason") or "").lower()
        is_hardware_check = any(
            kw in reason_lower for kw in ("hardware", "model", "platform", "chassis")
        )
        if only_show_version and not is_hardware_check:
            better = _feature_commands_from_title(title)
            if better:
                logger.info(
                    "Overriding 'show version' with feature commands for %s: %s",
                    advisory_id, better,
                )
                commands = better
                feature = f"checking if feature is configured (auto-derived from title)"
                _feature = feature
                _emit("plan", {"feature": feature, "commands": commands})

        commands = [_normalize_ios_command(c) for c in commands]

        # ══════════════════════════════════════════════════════════════════════
        # PHASE 2: Execute commands via SSH → LLM assessment
        # ══════════════════════════════════════════════════════════════════════

        for cmd in commands[:_MAX_COMMANDS]:
            _emit("step", {"command": cmd, "status": "running"})
            try:
                result     = run_cli.invoke({"host": hostname, "command": cmd})
                result_str = str(result)
            except Exception as ssh_exc:
                result_str = f"[SSH ERROR] {ssh_exc}"

            commands_run.append({"command": cmd, "output": result_str})
            _emit("step", {"command": cmd, "output": result_str, "status": "done"})

        # ── Programmatic pre-check: ALL commands empty → not_affected ─────
        #    Short-circuit only when every single command returned empty output.
        #    If ANY command has real output, always defer to LLM assessment.

        all_commands_empty = all(_cli_output_is_empty(cr["output"]) for cr in commands_run)
        if all_commands_empty:
            empty_cmds = ", ".join(f"'{cr['command']}'" for cr in commands_run)
            explanation = (
                f"Feature not configured. All commands ({empty_cmds}) returned empty output — "
                f"the required feature is not present on this device."
            )
            _emit("verdict", {"verdict": "not_affected", "confidence": 0.95, "explanation": explanation})
            return _result("not_affected", 0.95, explanation)

        # ── LLM Assess: compare output against advisory ───────────────────────

        _emit("status", {"message": "Analyzing results..."})

        evidence_parts = []
        for cr in commands_run:
            empty_tag = " [OUTPUT IS EMPTY — feature NOT configured]" if _cli_output_is_empty(cr["output"]) else ""
            evidence_parts.append(f"$ {cr['command']}{empty_tag}\n{cr['output']}")
        evidence = "\n\n".join(evidence_parts)

        assess_prompt = (
            "/no_think\n"
            f"You are a Cisco security engineer assessing whether a device is affected by a vulnerability.\n\n"
            f"=== ADVISORY ===\n"
            f"[{sir}] {title}\n"
            f"CVEs: {cves}\n\n"
            f"=== ADVISORY TEXT ===\n{advisory_text}\n\n"
            f"=== CLI OUTPUT FROM DEVICE {hostname} ({platform_label}) ===\n{evidence}\n\n"
            f"=== RULES ===\n"
            f"Based on the CLI output above, determine if this device is affected.\n\n"
            f"- 'show running-config | include X' with EMPTY output → feature NOT configured → not_affected\n"
            f"- 'show running-config | section X' with EMPTY output → feature NOT configured → not_affected\n"
            f"- Feature explicitly disabled (e.g. 'no ip http server') → not_affected\n"
            f"- Feature active / config lines present that MATCH the advisory's specific requirement → affected\n"
            f"- IMPORTANT: Config section having output does NOT automatically mean affected.\n"
            f"  The advisory may target a SPECIFIC SUB-FEATURE. Example:\n"
            f"  • Advisory targets SNMPv3 → 'snmp-server community' (v2c) is NOT affected, only 'snmp-server user' (v3) is\n"
            f"  • Advisory targets SSL VPN → basic crypto config is NOT affected, only 'webvpn' with 'inservice' is\n"
            f"  Read the advisory carefully and match the EXACT feature/sub-feature it describes.\n"
            f"- Hardware model does not match affected models → not_affected\n"
            f"- IOSv / virtual platform and advisory targets specific physical hardware → not_affected\n"
            f"- '% Invalid input' → command not supported → not_affected\n"
            f"- 'show snmp user' or 'show snmp group' with EMPTY output → no SNMPv3 users configured → not_affected for SNMPv3 advisories\n"
            f"- DO NOT imagine config lines not shown in the output\n"
            f"- Use 'uncertain' ONLY if ALL commands returned SSH errors\n\n"
            f"Return ONLY JSON:\n"
            f'{{\"verdict\":\"affected|not_affected\",\"confidence\":0.85-1.0,\"explanation\":\"cite the EXACT output\"}}'
        )

        llm_assess   = create_chat_model(reasoning=False)
        assess_reply = llm_assess.invoke([HumanMessage(content=assess_prompt)])
        final_text   = _strip_think(str(getattr(assess_reply, "content", "") or ""))

        data      = _safe_json(final_text, {})
        verdict   = data.get("verdict", "")
        if verdict not in ("affected", "not_affected", "uncertain"):
            has_cli = any(not cr["output"].startswith("[SSH ERROR]") for cr in commands_run)
            verdict = "not_affected" if has_cli else "uncertain"
        confidence  = min(1.0, max(0.0, float(data.get("confidence") or 0.85)))
        explanation = str(data.get("explanation") or final_text[:500] or "No explanation provided.")

        # ── Programmatic evidence override ───────────────────────────────
        #    If CLI output clearly proves affected but LLM said not_affected,
        #    override with programmatic evidence (prevents hallucination).

        if verdict != "affected":
            override = _check_port_listening(commands_run, advisory_text, title)
            if override:
                logger.warning(
                    "Overriding LLM verdict '%s' → 'affected' for %s: %s",
                    verdict, advisory_id, override["explanation"][:120],
                )
                verdict    = override["verdict"]
                confidence = override["confidence"]
                explanation = override["explanation"]

        _emit("verdict", {"verdict": verdict, "confidence": confidence, "explanation": explanation})

        return _result(verdict, confidence, explanation)

    except Exception as exc:
        logger.exception(
            "Advisory check failed for device=%s advisory=%s: %s",
            hostname, advisory.get("advisory_id"), exc,
        )
        _emit("error", {"message": str(exc)})
        return _result("uncertain", 0.0, f"Check failed: {exc}")
