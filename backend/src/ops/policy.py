"""Centralized policy and authority checks for ops actions."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from src.ops.action_catalog import ActionCatalogEntry

ROLE_LEVELS = {
    "viewer": 0,
    "operator": 1,
    "approver": 2,
    "admin": 3,
}

RISK_ORDER = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}

DEFAULT_APPROVAL_ROLE_BY_RISK = {
    "low": "approver",
    "medium": "approver",
    "high": "admin",
    "critical": "admin",
}

DEFAULT_EXECUTION_ROLE_BY_RISK = {
    "low": "operator",
    "medium": "approver",
    "high": "admin",
    "critical": "admin",
}

_READONLY_PREFIX_RE = re.compile(r"^\s*(show|sh\s|ping|traceroute|tracert|display)\b", re.IGNORECASE)
_BLOCK_RE = re.compile(
    r"^\s*(reload|reboot|write\s+erase|erase\s+startup-config|format|delete\s+/|"
    r"copy\s+.*\s+startup-config|archive\s+download-sw)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PolicyDecision:
    action_id: str
    label: str
    risk_level: str
    readonly: bool
    minimum_role: str
    required_approval_role: str
    required_execution_role: str
    readiness: str
    readiness_score: float
    allowed: bool
    reason: str
    blocked_conditions: list[str]

    def serialize(self) -> dict:
        return {
            "action_id": self.action_id,
            "label": self.label,
            "risk_level": self.risk_level,
            "readonly": self.readonly,
            "minimum_role": self.minimum_role,
            "required_approval_role": self.required_approval_role,
            "required_execution_role": self.required_execution_role,
            "readiness": self.readiness,
            "readiness_score": self.readiness_score,
            "allowed": self.allowed,
            "reason": self.reason,
            "blocked_conditions": list(self.blocked_conditions),
        }


def normalize_role(value: str | None) -> str:
    role = (value or "").strip().lower() or "viewer"
    return role if role in ROLE_LEVELS else "viewer"


def role_allows(actor_role: str | None, required_role: str) -> bool:
    return ROLE_LEVELS[normalize_role(actor_role)] >= ROLE_LEVELS[required_role]


def max_role(*roles: str) -> str:
    normalized = [normalize_role(role) for role in roles if role]
    if not normalized:
        return "viewer"
    return max(normalized, key=lambda role: ROLE_LEVELS[role])


def classify_command_set(commands: list[str]) -> tuple[bool, bool]:
    cleaned = [command.strip() for command in commands if command.strip()]
    if not cleaned:
        return True, False
    readonly = all(_READONLY_PREFIX_RE.search(command) for command in cleaned)
    blocked = any(_BLOCK_RE.search(command) for command in cleaned)
    return readonly, blocked


def _readiness_from_inputs(*, readonly: bool, blocked: bool, verify_count: int, rollback_count: int) -> tuple[str, float, str]:
    if blocked:
        return "blocked_pending_more_evidence", 0.1, "Command set matches a hard-block policy."
    if readonly:
        return "ready_for_human_review", 0.85, "Read-only diagnostics are ready once scope is reviewed."
    if verify_count <= 0:
        return "blocked_pending_more_evidence", 0.35, "Post-check commands are required before execution."
    if rollback_count <= 0:
        return "ready_for_human_review", 0.55, "Rollback plan is still missing, so review should stay manual."
    return "safe_for_low_risk_execution", 0.75, "Proposal includes verification and rollback coverage."


def policy_decision_for_proposal(
    *,
    action: ActionCatalogEntry,
    commands: list[str],
    verify_commands: list[str],
    rollback_commands: list[str],
    actor_role: str | None,
) -> PolicyDecision:
    readonly, blocked = classify_command_set(commands)
    minimum_role = action.minimum_role
    default_required_role = DEFAULT_APPROVAL_ROLE_BY_RISK[action.default_risk]
    required_approval_role = max(
        [action.approval_role, default_required_role],
        key=lambda role: ROLE_LEVELS[role],
    )
    required_execution_role = DEFAULT_EXECUTION_ROLE_BY_RISK[action.default_risk]
    readiness, readiness_score, reason = _readiness_from_inputs(
        readonly=readonly or action.readonly,
        blocked=blocked,
        verify_count=len(verify_commands),
        rollback_count=len(rollback_commands),
    )

    allowed = role_allows(actor_role, minimum_role)
    if not allowed:
        reason = f"Role '{normalize_role(actor_role)}' cannot create action '{action.action_id}'."

    return PolicyDecision(
        action_id=action.action_id,
        label=action.label,
        risk_level=action.default_risk,
        readonly=readonly or action.readonly,
        minimum_role=minimum_role,
        required_approval_role=required_approval_role,
        required_execution_role=required_execution_role,
        readiness=readiness,
        readiness_score=readiness_score,
        allowed=allowed,
        reason=reason,
        blocked_conditions=list(action.blocked_conditions),
    )


def require_role(actor_role: str | None, required_role: str, *, action: str) -> None:
    normalized = normalize_role(actor_role)
    if not role_allows(normalized, required_role):
        raise PermissionError(
            f"Role '{normalized}' cannot {action}. Required role: '{required_role}'."
        )


def approval_role_for_risk(risk_level: str) -> str:
    return DEFAULT_APPROVAL_ROLE_BY_RISK.get(risk_level, "approver")


def execution_role_for_risk(risk_level: str) -> str:
    return DEFAULT_EXECUTION_ROLE_BY_RISK.get(risk_level, "approver")


def dual_approval_required(risk_level: str) -> bool:
    threshold = os.getenv("OPS_DUAL_APPROVAL_RISK", "critical").strip().lower() or "critical"
    return RISK_ORDER.get(risk_level, 1) >= RISK_ORDER.get(threshold, 3)
