"""Structured action catalog for approval-gated operations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class ActionCatalogEntry:
    action_id: str
    label: str
    description: str
    category: str
    supported_platforms: list[str]
    required_params: list[str] = field(default_factory=list)
    default_risk: str = "medium"
    minimum_role: str = "operator"
    approval_role: str = "approver"
    readonly: bool = False
    prechecks: list[str] = field(default_factory=list)
    verify_steps: list[str] = field(default_factory=list)
    rollback_strategy: list[str] = field(default_factory=list)
    blocked_conditions: list[str] = field(default_factory=list)

    def serialize(self) -> dict:
        return asdict(self)


_CATALOG: dict[str, ActionCatalogEntry] = {
    "check_bgp_neighbor": ActionCatalogEntry(
        action_id="check_bgp_neighbor",
        label="Check BGP Neighbor",
        description="Collect read-only BGP neighbor diagnostics from a single device.",
        category="diagnostic",
        supported_platforms=["cisco_ios", "cisco_xe", "cisco_nxos"],
        required_params=["target_host", "neighbor"],
        default_risk="low",
        minimum_role="operator",
        approval_role="approver",
        readonly=True,
        prechecks=["show clock", "show ip bgp summary"],
        verify_steps=["show ip bgp summary", "show ip bgp neighbors {{neighbor}}"],
        rollback_strategy=["No rollback required for read-only diagnostics."],
        blocked_conditions=["Target device must be reachable over SSH."],
    ),
    "collect_interface_diagnostics": ActionCatalogEntry(
        action_id="collect_interface_diagnostics",
        label="Collect Interface Diagnostics",
        description="Gather read-only interface counters, status, and log context.",
        category="diagnostic",
        supported_platforms=["cisco_ios", "cisco_xe", "cisco_nxos"],
        required_params=["target_host", "interface_name"],
        default_risk="low",
        minimum_role="operator",
        approval_role="approver",
        readonly=True,
        prechecks=["show clock"],
        verify_steps=[
            "show interface {{interface_name}}",
            "show logging | include {{interface_name}}",
        ],
        rollback_strategy=["No rollback required for read-only diagnostics."],
        blocked_conditions=["Interface name must be specified."],
    ),
    "clear_bgp_soft": ActionCatalogEntry(
        action_id="clear_bgp_soft",
        label="Clear BGP Soft",
        description="Run a soft BGP clear for a single neighbor after review.",
        category="remediation",
        supported_platforms=["cisco_ios", "cisco_xe"],
        required_params=["target_host", "neighbor"],
        default_risk="medium",
        minimum_role="operator",
        approval_role="approver",
        readonly=False,
        prechecks=[
            "show ip bgp summary",
            "show ip route summary",
        ],
        verify_steps=[
            "show ip bgp summary",
            "show ip bgp neighbors {{neighbor}}",
        ],
        rollback_strategy=[
            "If adjacency does not recover, escalate and restore last known-good BGP configuration manually.",
        ],
        blocked_conditions=[
            "Do not run if neighbor is carrying critical transit without a maintenance window.",
        ],
    ),
    "bounce_tunnel_interface": ActionCatalogEntry(
        action_id="bounce_tunnel_interface",
        label="Bounce Tunnel Interface",
        description="Shutdown/no shutdown a tunnel interface after explicit approval.",
        category="remediation",
        supported_platforms=["cisco_ios", "cisco_xe"],
        required_params=["target_host", "interface_name"],
        default_risk="high",
        minimum_role="operator",
        approval_role="admin",
        readonly=False,
        prechecks=[
            "show interface {{interface_name}}",
            "show ip route {{neighbor_subnet}}",
        ],
        verify_steps=[
            "show interface {{interface_name}}",
            "show logging | include {{interface_name}}",
        ],
        rollback_strategy=[
            "Re-apply no shutdown if interface stays down.",
            "Validate dependent routing adjacencies after recovery.",
        ],
        blocked_conditions=[
            "Requires explicit maintenance intent and blast-radius review.",
        ],
    ),
    "restore_last_config": ActionCatalogEntry(
        action_id="restore_last_config",
        label="Restore Last Config",
        description="Restore a previously captured configuration snapshot.",
        category="rollback",
        supported_platforms=["cisco_ios", "cisco_xe"],
        required_params=["target_host", "config_snapshot_id"],
        default_risk="critical",
        minimum_role="operator",
        approval_role="admin",
        readonly=False,
        prechecks=[
            "show clock",
            "show archive",
        ],
        verify_steps=[
            "show archive",
            "show running-config | section",
        ],
        rollback_strategy=[
            "If restore fails, stop automation and escalate for manual recovery.",
        ],
        blocked_conditions=[
            "Only allowed when a valid config snapshot exists.",
            "Requires admin-level approval and a second reviewer.",
        ],
    ),
    "generic_readonly": ActionCatalogEntry(
        action_id="generic_readonly",
        label="Generic Read-only Action",
        description="Flexible read-only action proposed by AI or operator.",
        category="diagnostic",
        supported_platforms=["*"],
        default_risk="low",
        minimum_role="operator",
        approval_role="approver",
        readonly=True,
        prechecks=["Verify target scope before execution."],
        verify_steps=["Review command output for expected evidence."],
        rollback_strategy=["No rollback required for read-only diagnostics."],
        blocked_conditions=["Commands must stay within approved read-only patterns."],
    ),
    "generic_config_change": ActionCatalogEntry(
        action_id="generic_config_change",
        label="Generic Config Change",
        description="Flexible configuration proposal that still passes policy and approval gates.",
        category="change",
        supported_platforms=["*"],
        default_risk="medium",
        minimum_role="operator",
        approval_role="approver",
        readonly=False,
        prechecks=["Capture current state before execution."],
        verify_steps=["Run explicit post-check commands supplied with the proposal."],
        rollback_strategy=["Provide rollback commands or a rollback procedure before approval."],
        blocked_conditions=["Commands must not match blocked destructive patterns."],
    ),
}


def list_action_catalog() -> list[dict]:
    return [entry.serialize() for entry in sorted(_CATALOG.values(), key=lambda item: item.label)]


def get_action_catalog_entry(action_id: str | None) -> ActionCatalogEntry | None:
    if not action_id:
        return None
    return _CATALOG.get(action_id)
