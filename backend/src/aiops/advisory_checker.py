"""Advisory Impact Checker — LLM+SSH agent to verify if a device is actually affected by a Cisco PSIRT advisory."""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from typing import Any, Callable

logger = logging.getLogger(__name__)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
_MAX_ITERATIONS = int(os.getenv("ADVISORY_CHECK_MAX_ITERATIONS", "6"))


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


def check_advisory_impact(
    *,
    device: dict[str, Any],
    advisory: dict[str, Any],
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """
    Run LLM+SSH investigation to determine if `device` is actually affected by `advisory`.

    Returns dict with keys:
        verdict: "affected" | "not_affected" | "uncertain"
        confidence: float 0–1
        explanation: str
        commands_run: list[{command, output}]
    """
    commands_run: list[dict[str, str]] = []

    def _emit(event_type: str, data: dict[str, Any]) -> None:
        if on_event:
            on_event({"type": event_type, **data})

    fallback: dict[str, Any] = {
        "verdict": "uncertain",
        "confidence": 0.0,
        "explanation": "Check could not complete — LLM or SSH not available.",
        "commands_run": commands_run,
    }

    if not _llm_enabled():
        return fallback

    hostname = device.get("hostname", "")
    ip_address = device.get("ip_address", "")
    os_platform = device.get("os_platform", "cisco_ios")

    if not ip_address:
        fallback["explanation"] = f"Device {hostname} has no IP address configured."
        return fallback

    try:
        from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
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
        tools = [run_cli]
        tool_map = {"run_cli": run_cli}

        advisory_id = advisory.get("advisory_id", "")
        title = advisory.get("title", "")
        sir = advisory.get("sir", "")
        cvss_score = advisory.get("cvss_score", 0)
        cves = ", ".join(advisory.get("cves", [])[:5]) or advisory_id
        summary = (advisory.get("summary") or "")[:600]
        workaround = (advisory.get("workaround") or "")[:400]

        system_prompt = (
            "/no_think\n"
            f"You are a Cisco security analyst. Your job: determine if device {hostname} "
            f"(IOS: {device.get('version', 'unknown')}, platform: {os_platform}, "
            f"role: {device.get('device_role', 'unknown')}) "
            f"is actually affected by this Cisco PSIRT advisory.\n\n"
            f"Advisory: [{sir}] CVSS:{cvss_score} — {title}\n"
            f"CVEs: {cves}\n"
            f"Description: {summary}\n"
            f"Workaround hint: {workaround}\n\n"
            "Steps:\n"
            "1. Run 2–5 targeted READ-ONLY `show` commands to check if the vulnerable "
            "feature/service is active on this device.\n"
            "2. Analyze the output.\n"
            "3. Return ONLY this JSON (no prose before or after):\n"
            '   {"verdict":"affected|not_affected|uncertain","confidence":0.0-1.0,"explanation":"..."}\n\n'
            "Guidance by advisory type:\n"
            "- HTTP/HTTPS service → show ip http server status; show ip http secure-server\n"
            "- SSH server → show ip ssh\n"
            "- Smart Install → show vstack config\n"
            "- CDP → show cdp neighbors detail\n"
            "- NTP → show ntp status\n"
            "- OSPF → show ip ospf neighbor\n"
            "- BGP → show ip bgp summary\n"
            "- IKEv2/IPsec → show crypto isakmp sa\n"
            "- SNMP → show snmp\n"
            "- GRE/Tunnel → show interface Tunnel0\n"
            "- STP → show spanning-tree summary\n"
            "- VLAN/VTP → show vtp status\n"
            "- Netflow → show ip flow interface\n"
            "Always start with `show version` if you need to confirm the exact platform.\n"
            "NEVER run config commands. Max 5 commands total."
        )

        _emit("status", {"message": f"Analyzing {title[:70]}..."})

        model = create_chat_model(reasoning=False).bind_tools(tools)
        messages: list[Any] = [
            SystemMessage(content=system_prompt),
            HumanMessage(
                content=(
                    f"Check if {hostname} ({ip_address}) is affected by this advisory. "
                    "Run targeted show commands then return your verdict as JSON."
                )
            ),
        ]

        final_text = ""
        for _iteration in range(_MAX_ITERATIONS):
            reply = model.invoke(messages)
            messages.append(reply)

            content = str(getattr(reply, "content", "") or "")
            tool_calls = getattr(reply, "tool_calls", None) or []

            if not tool_calls:
                final_text = content
                break

            for tc in tool_calls:
                tc_name = tc.get("name", "")
                tc_args = tc.get("args", {})

                if tc_name == "run_cli":
                    command = tc_args.get("command", "")
                    _emit("step", {"command": command, "status": "running"})

                    result = run_cli.invoke(tc_args)
                    result_str = str(result)

                    commands_run.append({"command": command, "output": result_str[:2000]})
                    _emit("step", {
                        "command": command,
                        "output": result_str[:1200],
                        "status": "done",
                    })
                    messages.append(ToolMessage(
                        content=result_str[:3000],
                        tool_call_id=tc["id"],
                        name=tc_name,
                    ))
                else:
                    messages.append(ToolMessage(
                        content="[ERROR] Unknown tool",
                        tool_call_id=tc["id"],
                        name=tc_name,
                    ))

        # Parse verdict from LLM final message
        clean_text = _strip_think(final_text)
        data = _safe_json(clean_text, {})
        verdict = data.get("verdict", "uncertain")
        if verdict not in ("affected", "not_affected", "uncertain"):
            verdict = "uncertain"
        confidence = min(1.0, max(0.0, float(data.get("confidence") or 0.5)))
        explanation = str(data.get("explanation") or clean_text[:500] or "No explanation provided.")

        _emit("verdict", {
            "verdict": verdict,
            "confidence": confidence,
            "explanation": explanation,
        })

        return {
            "verdict": verdict,
            "confidence": confidence,
            "explanation": explanation,
            "commands_run": commands_run,
        }

    except Exception as exc:
        logger.exception(
            "Advisory check failed for device=%s advisory=%s: %s",
            hostname,
            advisory.get("advisory_id"),
            exc,
        )
        _emit("error", {"message": str(exc)})
        return {
            "verdict": "uncertain",
            "confidence": 0.0,
            "explanation": f"Check failed: {exc}",
            "commands_run": commands_run,
        }
