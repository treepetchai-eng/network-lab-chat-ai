# AGENTS.md

This repository is intentionally designed as an `LLM-first free-run` system.

Any agent editing code in this repo must preserve that design.

## Core Principle

The model should remain responsible for:

- understanding the user's intent
- deciding whether to use inventory tools
- deciding device scope
- choosing commands
- deciding when enough evidence has been gathered
- writing the final answer

The backend should remain responsible for:

- tool execution
- session lifecycle
- read-only safety
- preventing duplicate or invalid execution
- preserving structured evidence
- lightweight consistency guardrails

Do not move product behavior from the LLM into backend hardcoded rules unless it is required for safety, correctness, or determinism of already-executed facts.

## Required Architecture

Keep the system `free-run`.

- Do not re-introduce rigid supervisor-style routing unless explicitly requested.
- Do not replace free-run reasoning with large deterministic decision trees.
- Do not hardcode command selection per scenario if the LLM can choose safely.
- Do not hardcode final answer text per use case.

Allowed:

- prompt improvements
- better tool/result formatting
- richer structured metadata for tool outputs
- no-tool final synthesis passes over gathered evidence
- fact-consistency checks that preserve LLM-authored answers
- safety guardrails

Not allowed:

- scenario-specific answer templates that bypass the LLM
- backend code that directly writes user-facing conclusions for normal cases
- special-case logic for individual hostnames, sites, or one-off prompts
- brittle regex trees that replace reasoning rather than support it

## Prompt-First Policy

When behavior is wrong, prefer fixing in this order:

1. improve the prompt
2. improve the structure/quality of evidence sent to the LLM
3. add a final synthesis step that answers from evidence only
4. add minimal consistency repair using exact facts from executed tools

Use backend hardcoding only as a last resort.

If you add backend logic, it must be generic and intent-level, not scenario-level.

Good:

- "for batch checks, provide exact success/failure counts from tool results"
- "run a final no-tools synthesis pass after tool execution"

Bad:

- "if prompt contains 'test ssh ทุกตัว', force this exact response"
- "if hostname is HQ-DIST-GW02, print timeout summary"

## Evidence Handling Rules

Tool results must be preserved as evidence.

- Prefer structured metadata such as `host`, `command`, `status`, `error_type`.
- Keep raw tool output available for inspection.
- Let the LLM summarize from evidence instead of discarding evidence.
- If counts matter, compute exact counts from executed tool results and pass them to the LLM as facts.
- Do not let the backend invent operational conclusions that are not present in the evidence.

## Final Answer Rules

The final answer should still be produced by the LLM.

Acceptable support patterns:

- prompt guidance for answer mode
- final answer synthesis with tools disabled
- consistency repair that rewrites an LLM draft using exact evidence facts

Unacceptable patterns:

- bypassing the LLM and returning handcrafted prose for normal product behavior
- hardcoded summary strings for specific scenarios
- deterministic output branches that grow per use case

## Safety and Determinism

Safety and factual consistency are valid reasons to constrain behavior.

Examples of acceptable constraints:

- blocking unsafe commands
- blocking duplicate commands in the same turn
- stopping repeated retries after terminal SSH failure
- forcing exact count facts to match executed results

Examples of unacceptable constraints:

- replacing reasoning with static playbooks for common user questions
- encoding product UX behavior as ad hoc special cases

## Code Change Guidance

When editing code:

- prefer small, reversible changes
- preserve the current `LLM-first free-run` design
- avoid introducing hidden logic that changes user-facing behavior silently
- document why a guardrail exists when it could be mistaken for hardcoding
- keep fixes generic across similar intents

When adding logic, ask:

1. Is this helping the LLM reason better?
2. Is this preserving evidence fidelity?
3. Is this only enforcing safety or factual consistency?
4. Or am I replacing reasoning with hardcoded behavior?

If the answer is #4, do not implement it without explicit approval.

## Testing Expectations

Changes should be verified with:

- focused unit tests for the new behavior
- at least one realistic end-to-end or live test when behavior affects final answers

Tests should prefer checking:

- tool evidence is preserved correctly
- prompts include the intended guidance
- final answers remain consistent with executed results

Avoid tests that lock the product into brittle wording unless exact wording is the intended product requirement.

## Review Checklist

Before finishing, confirm:

- the system is still `LLM-first free-run`
- no new scenario-specific hardcoding was introduced
- prompts and evidence structure were preferred over static branching
- any deterministic logic is limited to safety or fact consistency
- final user-facing behavior still flows through the LLM

## In Case of Conflict

If a requested change would push the system away from `LLM-first free-run`, pause and call that out explicitly before implementing.

Default stance:

`Improve the LLM's guidance and evidence, do not replace the LLM.`

## AI Factory Expansion Rules

This repo may evolve from a chat-based network copilot into an event-driven
`AI Network Ops / AI Factory` system.

That expansion is allowed only if it preserves the same `LLM-first free-run`
principle.

### Scope Of The Expansion

The intended product direction is:

- syslog-driven incident awareness
- PostgreSQL-backed inventory and operational data
- AI-led investigation from evidence
- approval-gated config execution
- manager-style UI for jobs, incidents, and approvals

The system must still remain model-led for reasoning and conclusions.

### Data Architecture Rules

Use PostgreSQL as the operational system of record for structured product data.

Expected PostgreSQL data domains:

- inventory/devices
- normalized events
- incidents
- jobs/tasks
- approvals/change proposals
- audit metadata for executions and verification

Do not treat PostgreSQL as the first-choice long-term raw log archive unless
that is explicitly requested and retention/storage implications are designed.

### Inventory Rules

Inventory is expected to move from the local CSV into PostgreSQL in stages.

Rules:

- PostgreSQL should become the primary source of truth for device inventory.
- The CSV may remain temporarily as a seed/import source during migration.
- Do not store device login secrets in inventory tables.
- For this lab, shared credentials may remain in `backend/.env`.
- If per-device or production-grade secrets are introduced later, prefer a
  dedicated secret-management approach rather than expanding plain-text env use.

### Syslog And Event Rules

`syslog-ng` is the primary raw log collector.

Preferred near-term flow:

- devices send syslog to `syslog-ng`
- `syslog-ng` stores raw logs
- a dedicated ingestor reads from `syslog-ng` outputs
- backend stores structured event data in PostgreSQL
- AI consumes normalized events plus other evidence

Rules:

- Do not replace `syslog-ng` with ad hoc backend log collection unless
  explicitly requested.
- Prefer ingesting from existing `syslog-ng` outputs first.
- Keep raw log fidelity available either in `syslog-ng` storage or by storing
  enough raw evidence references for replay/debugging.
- Normalize events into structured fields such as device, severity,
  interface, protocol, and event type.
- Correlation logic must remain generic and evidence-driven, not scenario-
  specific hardcoding.

### Ingestion And Processing Rules

Do not bolt high-volume syslog ingestion directly onto normal chat request
handling.

Preferred pattern:

- background ingestor or worker for log import
- backend API for query/orchestration
- LLM agents for reasoning over stored evidence

If checkpoints are needed, store them explicitly so ingestion is resumable and
replay-friendly.

### Incident And Job Rules

Incidents and jobs are product concepts, not replacements for LLM reasoning.

Rules:

- incidents should group related evidence and provide context
- jobs/tasks should track orchestration state and auditability
- the backend may create/update these records deterministically
- the LLM should still interpret the evidence, decide next checks, and produce
  the user-facing analysis

Do not turn incidents/jobs into rigid playbooks that remove model judgment for
normal troubleshooting.

### Approval And Execution Rules

All write/config actions must be approval-gated.

Rules:

- read-only investigation may run without approval
- any config generation intended for execution must create a proposal record
- execution must require an approval state or approval token
- proposals should include targets, rationale, expected impact, rollback, and
  post-check intent
- verification results must be stored as evidence after execution

Do not allow autonomous config application by default.

### UI Direction Rules

The frontend may evolve beyond a pure chat page into a manager console.

Expected UI domains:

- dashboard
- chat/command center
- incidents
- devices/inventory
- jobs
- approvals

This UI expansion must not move operational reasoning into frontend-only logic.
The UI presents state and actions; the backend and LLM pipeline remain the
source of reasoning and evidence handling.

### Implementation Order

Unless explicitly overridden, prefer this sequence:

1. PostgreSQL connection and migrations
2. inventory migration from CSV to PostgreSQL
3. syslog ingestion from existing `syslog-ng` outputs
4. normalized events and event query APIs
5. incident records and correlation
6. UI for devices/events/incidents
7. AI investigation over stored evidence
8. approval workflow for proposed config changes
9. controlled execution and verification

This order is preferred because it improves the evidence layer first, which is
consistent with the prompt-first and evidence-first design of the repo.

### Architecture Review Check For Future Changes

For changes related to database, syslog, incidents, jobs, or approvals, confirm:

- does this preserve `LLM-first free-run` reasoning?
- does this improve or preserve evidence fidelity?
- does this keep raw log collection separate from operational structured data
  unless explicitly justified?
- does this avoid storing secrets in inventory records?
- does this keep write actions approval-gated?

If not, pause and realign before implementing.
