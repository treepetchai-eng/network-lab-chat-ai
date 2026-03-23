from __future__ import annotations

import json
import os
import re
import sys
import threading
from contextlib import contextmanager
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from src.aiops.parser import parse_syslog
from src.llm_factory import create_chat_model
from src.tools.cli_tool import create_run_cli_tool
from src.tools.inventory_tools import list_all_devices, lookup_device

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
_AIOPS_LLM_CONCURRENCY = max(1, int(os.getenv("AIOPS_LLM_CONCURRENCY", "1")))
_AIOPS_LLM_SLOT = threading.Semaphore(_AIOPS_LLM_CONCURRENCY)


@contextmanager
def _llm_slot():
    _AIOPS_LLM_SLOT.acquire()
    try:
        yield
    finally:
        _AIOPS_LLM_SLOT.release()


def _safe_json(value: str, fallback: dict[str, Any]) -> dict[str, Any]:
    match = _JSON_BLOCK_RE.search(value or "")
    if not match:
        return fallback
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return fallback


def _device_context(device: dict[str, Any] | None) -> dict[str, Any]:
    if not device:
        return {
            "hostname": "",
            "ip_address": "",
            "device_role": "",
            "site": "",
            "os_platform": "",
            "version": "",
        }
    return {
        "hostname": device.get("hostname", ""),
        "ip_address": device.get("ip_address", ""),
        "device_role": device.get("device_role", ""),
        "site": device.get("site", ""),
        "os_platform": device.get("os_platform", ""),
        "version": device.get("version", ""),
    }


def _device_context_text(device: dict[str, Any] | None) -> str:
    facts = _device_context(device)
    if not any(facts.values()):
        return "No inventory match was found for this source."
    if not device:
        return "No inventory match was found for this source."
    return (
        f"hostname={facts['hostname']}, "
        f"ip={facts['ip_address']}, "
        f"role={facts['device_role']}, "
        f"site={facts['site']}, "
        f"os={facts['os_platform']}, "
        f"version={facts['version']}"
    )


def _open_incident_context_text(open_incidents: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in open_incidents[:12]:
        lines.append(
            " | ".join([
                f"incident_no={item.get('incident_no','')}",
                f"title={item.get('title','')}",
                f"status={item.get('status','')}",
                f"family={item.get('event_family','')}",
                f"source={item.get('primary_source_ip','')}",
                f"key={item.get('correlation_key','')}",
            ])
        )
    return lines


def _incident_context(incident: dict[str, Any]) -> dict[str, Any]:
    return {
        "incident_no": incident.get("incident_no", ""),
        "title": incident.get("title", ""),
        "status": incident.get("status", ""),
        "severity": incident.get("severity", ""),
        "event_family": incident.get("event_family", ""),
        "primary_source_ip": incident.get("primary_source_ip", ""),
        "primary_hostname": incident.get("primary_hostname", ""),
        "site": incident.get("site", ""),
        "device_role": incident.get("device_role", ""),
        "os_platform": incident.get("os_platform", ""),
        "version": incident.get("version", ""),
        "event_count": incident.get("event_count", 0),
        "current_recovery_state": incident.get("current_recovery_state", ""),
        "category": incident.get("category", ""),
    }


def _incident_context_text(incident: dict[str, Any]) -> str:
    context = _incident_context(incident)
    return (
        f"incident_no={context['incident_no']}, "
        f"title={context['title']}, "
        f"status={context['status']}, "
        f"severity={context['severity']}, "
        f"family={context['event_family']}, "
        f"device={context['primary_hostname'] or context['primary_source_ip']}, "
        f"role={context['device_role']}, "
        f"site={context['site']}, "
        f"platform={context['os_platform']}, "
        f"version={context['version']}, "
        f"event_count={context['event_count']}, "
        f"recovery_state={context['current_recovery_state']}"
    )


def _rewrite_troubleshoot_result(result: dict[str, Any], incident: dict[str, Any]) -> dict[str, Any]:
    source = incident.get("primary_hostname") or incident["primary_source_ip"]
    disposition = result.get("disposition", "needs_human_review")
    summary = (result.get("summary") or "").strip()
    conclusion = (result.get("conclusion") or "").strip()
    if not summary:
        summary = f"Assessment for {source} remains incomplete and still requires targeted verification."
    if not conclusion:
        conclusion = "No conclusive remediation path was established from the available evidence."

    result["summary"] = f"Assessment: {summary}"
    result["conclusion"] = f"Engineering judgment: {conclusion}"

    if disposition == "physical_issue":
        result["conclusion"] += " Current evidence leans toward underlay or hardware involvement, so escalation should take precedence over config change."
    elif disposition == "config_fix_possible":
        result["conclusion"] += " A configuration-based remediation path appears plausible, but it should remain approval-gated and verification-driven."
    elif disposition == "self_recovered":
        result["conclusion"] += " Recovery signals exist, but stability should still be validated before closure."
    return result


def _extract_bgp_peer(incident: dict[str, Any]) -> str:
    text = " ".join([
        incident.get("title", ""),
        incident.get("summary", ""),
        *(log.get("raw_message", "") for log in incident.get("recent_logs", [])[:8]),
    ])
    match = re.search(r"neighbor\s+(\d+\.\d+\.\d+\.\d+)", text, re.IGNORECASE)
    return match.group(1) if match else ""


def _monitoring_rationale(incident: dict[str, Any]) -> str:
    status = incident.get("status", "")
    recovery_state = incident.get("current_recovery_state", "")
    if status == "monitoring":
        return "Recovery signals have been observed, but the incident is being held open until stability is confirmed across the monitoring window."
    if status == "recovering":
        return "A recovery signal exists, but the service state still needs verification before the incident can be closed."
    if recovery_state == "watching":
        return "The platform is still watching for repeat failure or additional evidence before downgrading the incident."
    return ""


def _fallback_summary(incident: dict[str, Any]) -> dict[str, Any]:
    category = "physical" if incident["event_family"] in {"device_health", "interface"} else "logical"
    return {
        "summary": _engineer_summary(incident),
        "probable_cause": _engineer_cause(incident),
        "confidence_score": _engineer_confidence(incident),
        "category": category,
        "impact": _engineer_impact(incident),
        "suggested_checks": _engineer_checks(incident),
    }


def _engineer_confidence(incident: dict[str, Any]) -> float:
    if incident["event_family"] in {"bgp", "ospf", "eigrp", "tunnel", "tracking"}:
        return 0.67
    if incident["event_family"] == "interface":
        return 0.72
    return 0.5


def _engineer_summary(incident: dict[str, Any]) -> str:
    family = incident["event_family"]
    source = incident.get("primary_hostname") or incident["primary_source_ip"]
    event_count = incident["event_count"]
    title = incident["title"]
    peer = _extract_bgp_peer(incident)
    monitoring_note = _monitoring_rationale(incident)
    if family == "bgp":
        base = (
            f"BGP adjacency toward {peer} on {source} dropped and the incident now aggregates {event_count} related control-plane event(s). "
            "Treat this as a routing stability event until neighbor state, reset counters, and transport reachability are revalidated."
            if peer
            else f"BGP adjacency on {source} dropped and the incident now aggregates {event_count} related control-plane event(s). "
            "Treat this as a routing stability event until neighbor state, reset counters, and transport reachability are revalidated."
        )
        return f"{base} {monitoring_note}".strip()
    if family == "tunnel":
        base = (
            f"Tunnel-state telemetry on {source} indicates an overlay reachability transition. "
            f"There are {event_count} correlated event(s) attached, which is enough to justify verifying tunnel state, "
            "routing adjacency over the tunnel, and whether this is a transient flap or an unrecovered outage."
        )
        return f"{base} {monitoring_note}".strip()
    if family == "tracking":
        base = (
            f"Tracking or IP SLA state changed on {source}. "
            f"Because {event_count} related event(s) are already correlated, the likely concern is path selection or failover behavior rather than a cosmetic log only."
        )
        return f"{base} {monitoring_note}".strip()
    if family == "interface":
        base = (
            f"Interface telemetry on {source} shows an operational state change. "
            f"With {event_count} correlated event(s), this should be treated as a possible underlay fault until line protocol, carrier state, and downstream protocol recovery are confirmed."
        )
        return f"{base} {monitoring_note}".strip()
    return f"{title}. The system currently correlates {event_count} related event(s) and this incident still requires investigation. {monitoring_note}".strip()


def _engineer_cause(incident: dict[str, Any]) -> str:
    family = incident["event_family"]
    peer = _extract_bgp_peer(incident)
    if family == "bgp":
        if peer:
            return f"The leading hypothesis is loss of BGP establishment toward peer {peer}, likely due to transport reachability degradation, session reset, policy mismatch, or peer-side instability."
        return "The leading hypothesis is loss of BGP establishment caused by transport reachability degradation, session reset, policy mismatch, or peer-side instability."
    if family == "tunnel":
        return "Most likely a tunnel underlay reachability issue, keepalive failure, or a dependency failure in the overlay control plane."
    if family == "tracking":
        return "Most likely an IP SLA probe failure or tracked object transition that may be influencing default-path or failover logic."
    if family == "interface":
        return "Most likely a physical or Layer 2 state change such as carrier loss, remote-side shutdown, cabling issue, or unstable line protocol."
    if family in {"ospf", "eigrp"}:
        return "Most likely a routing adjacency failure due to hello/dead timeout, transport loss, interface state change, or control-plane instability."
    return "The current evidence is still limited to syslog, so root cause should be treated as a working hypothesis pending read-only verification."


def _engineer_impact(incident: dict[str, Any]) -> str:
    family = incident["event_family"]
    source = incident.get("primary_hostname") or incident["primary_source_ip"]
    status = incident.get("status", "")
    if family == "bgp":
        suffix = " The incident is currently in monitoring, so route exchange may already be back but still needs stability validation." if status == "monitoring" else ""
        return f"{source} may have lost external or inter-domain route exchange, which can affect reachability, path preference, and upstream convergence.{suffix}"
    if family == "tunnel":
        return f"Traffic using the affected tunnel on {source} may be degraded, rerouted, or fully interrupted depending on failover coverage."
    if family == "tracking":
        return f"Tracked-path behavior on {source} may no longer reflect design intent, which can affect failover, default route selection, or traffic symmetry."
    if family == "interface":
        return f"Services depending on the affected interface on {source} may be partially or fully impacted, including any routing neighbors or overlays built on top of it."
    return f"The affected node {source} may be experiencing degraded control-plane or transport behavior."


def _engineer_checks(incident: dict[str, Any]) -> list[str]:
    family = incident["event_family"]
    source = incident.get("primary_hostname") or incident["primary_source_ip"]
    peer = _extract_bgp_peer(incident)
    if family == "bgp":
        return [
            f"On {source}, check `show ip bgp summary` and verify the neighbor state, uptime, and reset counters{f' for peer {peer}' if peer else ''}.",
            "Confirm basic reachability toward the peer and whether there was a preceding interface, tunnel, or tracking event.",
            "Review recent raw logs for notification, hold timer expiry, transport resets, and any recovery signal before proposing config action.",
        ]
    if family == "tunnel":
        return [
            f"On {source}, verify `show interface tunnel` and confirm whether the tunnel is administratively up and operationally stable.",
            "Check the relevant routing neighbor state across the tunnel and confirm underlay reachability to the remote endpoint.",
            "Hold the incident in monitoring if an up event appears, because tunnel recovery can flap before fully stabilizing.",
        ]
    if family == "tracking":
        return [
            f"On {source}, inspect `show track` and the associated `show ip sla statistics` output.",
            "Confirm whether default-path or failover routing changed as a result of the tracked object transition.",
            "Do not suggest config change until probe target reachability and underlay condition are verified.",
        ]
    if family == "interface":
        return [
            f"On {source}, run `show interface` for the impacted port and verify line protocol, error counters, and recent transitions.",
            "Check whether any routing adjacency, trunk, or overlay service sits on top of the same interface.",
            "Escalate as physical if the interface remains down without evidence of a safe logical remediation path.",
        ]
    return [
        "Inspect the latest related raw logs and confirm whether the signal is isolated or part of a wider chain.",
        "Run targeted read-only show commands on the impacted device before deciding between monitoring, escalation, or remediation.",
        "Treat any recovery signal as provisional until state is revalidated.",
    ]


def _llm_enabled() -> bool:
    if os.getenv("AIOPS_DISABLE_LLM", "").strip() == "1":
        return False
    if "pytest" in sys.modules or os.getenv("PYTEST_CURRENT_TEST"):
        return False
    return True


def decide_incident_action(
    *,
    source_ip: str,
    hostname: str | None,
    raw_message: str,
    event_time: Any,
    device: dict[str, Any] | None,
    open_incidents: list[dict[str, Any]],
) -> dict[str, Any]:
    fallback = _fallback_incident_decision(
        source_ip=source_ip,
        hostname=hostname,
        raw_message=raw_message,
        event_time=event_time,
        device=device,
        open_incidents=open_incidents,
    )
    if not _llm_enabled():
        return fallback

    prompt = {
        "device": _device_context(device),
        "device_context": _device_context_text(device),
        "source_ip": source_ip,
        "hostname": hostname,
        "raw_message": raw_message,
        "event_time": str(event_time),
        "open_incidents": [
            {
                "incident_no": item["incident_no"],
                "title": item["title"],
                "status": item["status"],
                "event_family": item["event_family"],
                "correlation_key": item["correlation_key"],
                "primary_source_ip": item["primary_source_ip"],
            }
            for item in open_incidents[:12]
        ],
        "open_incident_context": _open_incident_context_text(open_incidents),
    }
    try:
        with _llm_slot():
            model = create_chat_model(reasoning=False)
            response = model.invoke([
                SystemMessage(
                    content=(
                        "You are the incident-decision engine for a network AIOps platform and you must think like a senior network operations engineer. "
                        "Treat the raw syslog as operational evidence, not just text classification input. "
                        "Use device identity, site, role, platform, and version to decide whether this event is operationally significant. "
                        "Correlate with currently open incidents only when the source, technology domain, and failure pattern genuinely align. "
                        "Prefer precise operational language such as adjacency loss, tunnel instability, tracked-path transition, or interface state change. "
                        "Avoid vague wording like issue detected, problem happened, or something may be wrong. "
                        "If the evidence is weak, you may still create an incident, but the summary must sound like a measured engineering hypothesis. "
                        "Return strict JSON with keys: action, incident_no, title, event_family, event_state, severity, "
                        "summary, correlation_key, category, reasoning, metadata. "
                        "Valid action values: create_incident, update_incident, ignore. "
                        "Valid categories: physical, logical, config-related, external, unknown. "
                        "The reasoning field should explain the operational basis for the decision in 1-3 concise engineer-style sentences."
                    )
                ),
                HumanMessage(content=json.dumps(prompt, ensure_ascii=False)),
            ])
        text = str(getattr(response, "content", "") or "")
        parsed = _safe_json(text, {})
        if parsed:
            parsed["raw_response"] = text
            for key, value in fallback.items():
                parsed.setdefault(key, value)
            if parsed.get("action") not in {"create_incident", "update_incident", "ignore"}:
                parsed["action"] = fallback["action"]
            return parsed
    except Exception:
        pass
    return fallback


def decide_incident_bundle(
    *,
    candidate_group: dict[str, Any],
    events: list[dict[str, Any]],
    raw_logs: list[dict[str, Any]],
    device: dict[str, Any] | None,
    open_incidents: list[dict[str, Any]],
) -> dict[str, Any]:
    fallback = _fallback_bundle_decision(
        candidate_group=candidate_group,
        events=events,
        raw_logs=raw_logs,
        device=device,
        open_incidents=open_incidents,
    )
    if not _llm_enabled():
        return fallback

    latest_event = events[0] if events else {}
    prompt = {
        "candidate_group": {
            "id": candidate_group.get("id"),
            "source_ip": candidate_group.get("source_ip"),
            "hostname": candidate_group.get("hostname"),
            "event_family": candidate_group.get("event_family"),
            "correlation_key": candidate_group.get("correlation_key"),
            "event_count": candidate_group.get("event_count"),
            "severity_rollup": candidate_group.get("severity_rollup"),
            "latest_event_state": candidate_group.get("latest_event_state"),
            "recovery_seen": candidate_group.get("recovery_seen"),
            "first_event_at": str(candidate_group.get("first_event_at")),
            "last_event_at": str(candidate_group.get("last_event_at")),
        },
        "device": _device_context(device),
        "device_context": _device_context_text(device),
        "latest_event": {
            "title": latest_event.get("title"),
            "summary": latest_event.get("summary"),
            "event_family": latest_event.get("event_family"),
            "event_state": latest_event.get("event_state"),
            "severity": latest_event.get("severity"),
            "metadata": latest_event.get("metadata") or {},
        },
        "recent_events": [
            {
                "title": item.get("title"),
                "summary": item.get("summary"),
                "event_family": item.get("event_family"),
                "event_state": item.get("event_state"),
                "severity": item.get("severity"),
                "metadata": item.get("metadata") or {},
                "created_at": str(item.get("created_at")),
            }
            for item in events[:10]
        ],
        "recent_raw_logs": [row.get("raw_message", "") for row in raw_logs[:10]],
        "open_incidents": [
            {
                "incident_no": item.get("incident_no"),
                "title": item.get("title"),
                "status": item.get("status"),
                "event_family": item.get("event_family"),
                "correlation_key": item.get("correlation_key"),
                "primary_source_ip": item.get("primary_source_ip"),
                "primary_hostname": item.get("primary_hostname"),
            }
            for item in open_incidents[:12]
        ],
        "open_incident_context": _open_incident_context_text(open_incidents),
    }
    try:
        with _llm_slot():
            model = create_chat_model(reasoning=False)
            response = model.invoke([
                SystemMessage(
                    content=(
                        "You are the incident-decision engine for a network AIOps platform and you reason from grouped evidence, not a single log line. "
                        "You are given a candidate group of related network events that were pre-grouped deterministically by correlation hints and time window. "
                        "Your task is to decide whether this candidate should create a new incident, update an existing incident, merge into an open incident, or be ignored. "
                        "Use device role, site, platform, recovery signals, and the sequence of events to judge whether this is a real operational thread. "
                        "Treat the pre-grouping as a helpful bundle, not as final truth. "
                        "Return strict JSON with keys: action, incident_no, title, event_family, event_state, severity, summary, correlation_key, category, reasoning, metadata. "
                        "Valid action values: create_incident, update_incident, ignore. "
                        "Valid categories: physical, logical, config-related, external, unknown."
                    )
                ),
                HumanMessage(content=json.dumps(prompt, ensure_ascii=False)),
            ])
        text = str(getattr(response, "content", "") or "")
        parsed = _safe_json(text, {})
        if parsed:
            parsed["raw_response"] = text
            for key, value in fallback.items():
                parsed.setdefault(key, value)
            if parsed.get("action") not in {"create_incident", "update_incident", "ignore"}:
                parsed["action"] = fallback["action"]
            return parsed
    except Exception:
        pass
    return fallback


def _fallback_incident_decision(
    *,
    source_ip: str,
    hostname: str | None,
    raw_message: str,
    event_time: Any,
    device: dict[str, Any] | None,
    open_incidents: list[dict[str, Any]],
) -> dict[str, Any]:
    parsed = parse_syslog(source_ip=source_ip, hostname=hostname, raw_message=raw_message, event_time=event_time)
    matched_incident = next(
        (
            item for item in open_incidents
            if item["correlation_key"] == parsed["correlation_key"]
        ),
        None,
    )
    category = "physical" if parsed["event_family"] in {"device_health", "interface"} else "logical"
    return {
        "action": "update_incident" if matched_incident else "create_incident",
        "incident_no": matched_incident["incident_no"] if matched_incident else None,
        "title": parsed["title"],
        "event_family": parsed["event_family"],
        "event_state": parsed["event_state"],
        "severity": parsed["severity"],
        "summary": parsed["summary"],
        "correlation_key": parsed["correlation_key"],
        "category": category,
        "reasoning": "Fallback incident decision based on parser heuristics because the LLM decision layer was unavailable.",
        "metadata": parsed["metadata"],
        "raw_response": "",
    }


def _fallback_bundle_decision(
    *,
    candidate_group: dict[str, Any],
    events: list[dict[str, Any]],
    raw_logs: list[dict[str, Any]],
    device: dict[str, Any] | None,
    open_incidents: list[dict[str, Any]],
) -> dict[str, Any]:
    latest_event = events[0] if events else {}
    correlation_key = candidate_group.get("correlation_key") or latest_event.get("correlation_key") or ""
    matched_incident = next(
        (
            item for item in open_incidents
            if item.get("correlation_key") == correlation_key
        ),
        None,
    )
    event_family = latest_event.get("event_family") or candidate_group.get("event_family") or "syslog"
    event_state = latest_event.get("event_state") or candidate_group.get("latest_event_state") or "info"
    severity = latest_event.get("severity") or candidate_group.get("severity_rollup") or "warning"
    title = latest_event.get("title") or f"{event_family.replace('_', ' ').title()} {event_state}"
    summary = latest_event.get("summary") or (raw_logs[0].get("raw_message", "") if raw_logs else title)
    category = "physical" if event_family in {"device_health", "interface"} else "logical"
    return {
        "action": "update_incident" if matched_incident else "create_incident",
        "incident_no": matched_incident.get("incident_no") if matched_incident else None,
        "title": title,
        "event_family": event_family,
        "event_state": event_state,
        "severity": severity,
        "summary": summary,
        "correlation_key": correlation_key,
        "category": category,
        "reasoning": "Fallback bundle decision based on normalized event grouping because the LLM decision layer was unavailable.",
        "metadata": latest_event.get("metadata") or {},
        "raw_response": "",
    }


def generate_ai_summary(incident: dict[str, Any], logs: list[dict[str, Any]]) -> dict[str, Any]:
    incident = {
        **incident,
        "recent_logs": logs,
    }
    prompt = {
        "incident": _incident_context(incident),
        "incident_context": _incident_context_text(incident),
        "recent_logs": [row["raw_message"] for row in logs[:8]],
        "recovery_expectation": _monitoring_rationale(incident),
    }
    if _llm_enabled():
        try:
            with _llm_slot():
                model = create_chat_model(reasoning=False)
                response = model.invoke([
                    SystemMessage(
                        content=(
                            "You are a senior network operations engineer writing the incident triage note that another engineer will act on immediately. "
                            "Write like an expert operator who understands control-plane behavior, underlay versus overlay dependency, and escalation boundaries. "
                            "Use the provided device role, site, platform, and incident context heavily when they are available. "
                            "Read only the provided syslog evidence and do not invent commands or observations that were not seen. "
                            "Return strict JSON with keys: summary, probable_cause, confidence_score, "
                            "category, impact, suggested_checks. "
                            "Make the summary technically specific, concise, and operationally useful. "
                            "When possible, mention the concrete peer, interface, tunnel, or tracked object seen in the logs. "
                            "If the incident status is monitoring or recovering, say why the incident is still open instead of describing it as a fresh outage. "
                            "Do not repeat the same sentence structure across summary, cause, and impact. "
                            "The probable_cause should read like a disciplined working hypothesis, not a guess. "
                            "The impact must describe likely blast radius in engineer language. "
                            "Suggested checks should be concrete and realistic for a network operator. "
                            "Categories must be one of physical, logical, config-related, external, unknown."
                        )
                    ),
                    HumanMessage(content=json.dumps(prompt, ensure_ascii=False)),
                ])
            text = str(getattr(response, "content", "") or "")
            parsed = _safe_json(text, {})
            if parsed:
                parsed["confidence_score"] = float(parsed.get("confidence_score", 0.5))
                parsed["suggested_checks"] = list(parsed.get("suggested_checks", []))
                parsed["raw_response"] = text
                return parsed
        except Exception:
            pass

    fallback = _fallback_summary(incident)
    fallback["raw_response"] = json.dumps(prompt, ensure_ascii=False)
    return fallback


def _execute_tool(tool_map: dict[str, Any], tool_call: dict[str, Any]) -> tuple[str, str]:
    tool_name = tool_call["name"]
    args = tool_call.get("args", {}) or {}
    result = tool_map[tool_name].invoke(args)
    return tool_name, result


def run_llm_troubleshoot(
    incident: dict[str, Any],
    logs: list[dict[str, Any]],
    device_cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    fallback = {
        "status": "completed",
        "disposition": "needs_human_review",
        "summary": "Investigation did not complete with high confidence.",
        "conclusion": "Use the incident evidence, raw logs, and manual CLI checks to continue.",
        "steps": [],
        "proposal": None,
        "raw_response": "",
    }
    if not device_cache:
        return fallback

    target_host = next(
        (
            record["hostname"]
            for record in device_cache.values()
            if record["ip_address"] == incident["primary_source_ip"]
        ),
        next(iter(device_cache)),
    )
    tools = [lookup_device, list_all_devices, create_run_cli_tool(device_cache)]
    tool_map = {tool.name: tool for tool in tools}

    prompt = {
        "incident": _incident_context(incident),
        "incident_context": _incident_context_text(incident),
        "target_host": target_host,
        "target_device": _device_context(device_cache.get(target_host)),
        "logs": [row["raw_message"] for row in logs[:8]],
    }

    if _llm_enabled():
        try:
            with _llm_slot():
                model = create_chat_model(reasoning=True).bind_tools(tools)
                messages: list[Any] = [
                    SystemMessage(
                        content=(
                            "You are an LLM-first network troubleshooting agent operating as a senior network engineer. "
                            "Your job is to turn incident evidence into an engineer-grade assessment using read-only investigation only. "
                            "Use the target device role, site, platform, and version to shape your troubleshooting path and conclusion. "
                            "Write like an expert who understands routing, failover, adjacency state, underlay versus overlay dependency, and operational risk. "
                            "Use tools when needed. Only read-only investigation is allowed. "
                            "CRITICAL TOOL USAGE RULES: "
                            "1. The 'command' argument to run_cli must be a single, complete IOS EXEC command on ONE line. "
                            "   ALWAYS include the full command verb. Examples: 'show interface Tunnel10', 'show ip eigrp neighbors', 'show ip route'. "
                            "2. NEVER pass a bare interface name like 'tunnel10' or 'GigabitEthernet0/1' as the command — that is NOT a valid IOS command. "
                            "3. NEVER embed newlines or multiple commands in a single 'command' argument — send one command per tool call. "
                            "4. When referencing interfaces in commands, always use the full form: 'show interface Tunnel10' not 'sh int tu10'. "
                            "5. If run_cli returns only the device header with no output below it, the command may be unsupported or returned empty. "
                            "   Try a different related command rather than repeating the same one. "
                            "Prefer lightweight commands such as 'show interface Tunnel10', 'show ip bgp summary', "
                            "'show ip ospf neighbor', 'show ip eigrp neighbors', 'show track', 'show ip route'. "
                            "When you are done, return strict JSON with keys: disposition, summary, "
                            "conclusion, proposed_fix_title, proposed_fix_rationale, proposed_commands, "
                            "rollback_plan, expected_impact, verification_commands. "
                            "The summary must read like a senior engineer status note. "
                            "The conclusion must read like a senior engineer judgment that clearly states whether to monitor, escalate, or propose remediation. "
                            "If the evidence points to physical or provider fault, say so directly and avoid configuration suggestions. "
                            "If you propose configuration changes, keep them minimal, reversible, and tied to the evidence. "
                            "Disposition must be one of self_recovered, monitor_further, physical_issue, "
                            "external_issue, config_fix_possible, needs_human_review."
                        )
                    ),
                    HumanMessage(content=json.dumps(prompt, ensure_ascii=False)),
                ]
                steps: list[dict[str, Any]] = []
                final_text = ""
                for _ in range(5):
                    reply = model.invoke(messages)
                    messages.append(reply)
                    tool_calls = getattr(reply, "tool_calls", None) or []
                    if not tool_calls:
                        final_text = str(getattr(reply, "content", "") or "")
                        break
                    for tool_call in tool_calls:
                        tool_name, result = _execute_tool(tool_map, tool_call)
                        steps.append({
                            "tool_name": tool_name,
                            "args": tool_call.get("args", {}),
                            "content": result,
                        })
                        messages.append(ToolMessage(content=result, tool_call_id=tool_call["id"], name=tool_name))

            data = _safe_json(final_text, {})
            if not data:
                fallback["steps"] = steps
                fallback["raw_response"] = final_text
                return fallback

            proposal = None
            if data.get("disposition") == "config_fix_possible":
                proposal = {
                    "title": data.get("proposed_fix_title") or f"Proposed fix for {incident['incident_no']}",
                    "rationale": data.get("proposed_fix_rationale") or "LLM-generated remediation proposal.",
                    "commands": list(data.get("proposed_commands") or []),
                    "rollback_plan": data.get("rollback_plan") or "Rollback the change if verification fails.",
                    "expected_impact": data.get("expected_impact") or "Should restore the affected logical state.",
                    "verification_commands": list(data.get("verification_commands") or []),
                    "target_devices": [target_host],
                }

            return _rewrite_troubleshoot_result({
                "status": "completed",
                "disposition": data.get("disposition", "needs_human_review"),
                "summary": data.get("summary") or "Troubleshooting completed.",
                "conclusion": data.get("conclusion") or "No detailed conclusion was returned.",
                "steps": steps,
                "proposal": proposal,
                "raw_response": final_text,
            }, incident)
        except Exception as exc:
            fallback["summary"] = f"Investigation fell back after an LLM/tooling error: {exc}"
            return fallback
    return _rewrite_troubleshoot_result(fallback, incident)
