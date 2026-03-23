# Product Requirements Document

## Product Name

Network Lab Chat AI

## Document Status

Draft v1

## Overview

Network Lab Chat AI is an LLM-first web application for network operations in a lab environment. The product combines two core experiences in one system:

1. A network copilot chat workspace that can understand operator questions, decide how to ground on devices, run safe read-only CLI commands, and stream answers back in real time.
2. An AIOps incident console that ingests syslog, normalizes events, groups related signals into incidents, and helps operators review evidence, triage issues, and approve controlled remediation flows.

The product target is to reduce operator effort from raw device data or raw syslog to an actionable operational answer.

## Problem Statement

Network troubleshooting in labs is slow and fragmented. Operators often need to:

- decide which devices matter
- look up inventory manually
- SSH to multiple devices
- choose the right commands
- correlate raw output by hand
- review syslog separately from chat-based troubleshooting

This creates slow triage loops, inconsistent analysis quality, and unnecessary context switching.

## Product Vision

Deliver a single web interface where a network operator can move from question or log signal to evidence-backed understanding, and then to guided operational action, without leaving the product.

## Goals

- Make network troubleshooting faster through an LLM-first operator workflow.
- Let the system reason over device inventory and live CLI evidence instead of returning generic answers.
- Ingest raw syslog and surface it as operator-readable logs, normalized events, and incident groupings.
- Keep risky actions guarded by explicit approval steps.
- Present live operational state in a web UI that is easy to scan and use during investigation.

## Non-Goals

- Full enterprise ITSM replacement
- Production-grade autonomous remediation without approval
- Full observability platform for metrics, traces, and packet analytics
- Multi-tenant RBAC-heavy enterprise platform in the current phase

## Target Users

### Primary Users

- Network engineers operating a lab
- Operators validating topology, routing, adjacency, and interface behavior
- Engineers triaging syslog-derived incidents

### Secondary Users

- Demo or PoC stakeholders reviewing incident workflows
- Engineers testing LLM-assisted troubleshooting flows

## Core User Jobs

### Chat Copilot

- Ask questions about one device, many devices, or the whole lab
- Request physical or logical topology understanding
- Investigate reachability, routing, and protocol state
- Receive a concise final answer backed by CLI evidence

### AIOps Console

- View recent raw logs from the database
- Inspect parsed events and grouped incidents
- Review AI-generated incident summaries
- Run troubleshooting for an incident
- Approve or reject remediation proposals before execution

## Product Scope

## In Scope

- Web chat UI with streaming answers
- FastAPI backend with SSE streaming
- Inventory-aware network grounding
- Safe read-only CLI execution through SSH
- Syslog ingestion endpoint
- Raw log storage in PostgreSQL
- Event parsing and correlation
- Incident list, detail, logs, approvals, devices, and history pages
- Approval-gated execution workflow

## Out of Scope For Now

- Production device fleet scale
- Fine-grained auth and permissions
- Audit-grade compliance workflows
- Multi-source telemetry fusion beyond current syslog and CLI evidence

## Functional Requirements

### 1. Chat Copilot

- Users can create a session and send chat messages.
- The backend must stream intermediate and final responses over SSE.
- The model must decide when to ground via inventory and when to run CLI tools.
- Unsafe commands must be blocked.
- The UI must show tool progress and streamed assistant text.

### 2. Inventory and Grounding

- The system must read device inventory from the backend inventory source.
- The model must be able to select one device, multiple devices, or all devices depending on user intent.
- Device metadata such as hostname, IP, role, site, and platform should inform answers.

### 3. Syslog Ingestion

- The backend must accept syslog-like payloads over an HTTP ingest endpoint.
- Incoming logs must be stored as raw evidence in PostgreSQL.
- Logs must enter an ingestion pipeline for parsing and grouping.

### 4. Event Parsing and Incident Correlation

- Raw logs must be normalized into events where possible.
- Events must be grouped into candidate groups and then incidents.
- The system should support incident updates as related events continue to arrive.
- AI summaries should stay attached to incident evidence.

### 5. AIOps Web Console

- Dashboard must show current operational state at a glance.
- Logs page must show raw logs and normalized events from the database.
- Incident pages must show evidence, timeline, summary, and workflow state.
- Approvals page must show pending remediation proposals.
- Devices page must show inventory-linked operational context.
- History page must show resolved incidents.

### 6. Troubleshooting and Remediation

- Users must be able to trigger AI troubleshooting from an incident.
- The system may propose remediation steps, but must not execute them without explicit approval.
- Execution results and verification state must be stored and visible.

## UX Requirements

- The interface should feel operational, not consumer-chat generic.
- Streaming should provide responsive feedback during analysis.
- Logs and evidence must be readable and scan-friendly.
- Operators should be able to jump quickly from dashboard to incident to logs.
- Empty states should explain whether data is absent or still processing.

## Technical Requirements

- Frontend: Next.js, React, TypeScript
- Backend: FastAPI, Python
- Database: PostgreSQL
- LLM runtime: provider-configurable via environment variables
- Network execution: SSH with read-only safety controls by default
- Data flow: session/chat stream plus AIOps ingest/correlation pipeline

## Success Metrics

- Time from user question to first streamed output
- Time from raw log ingest to visibility on the web UI
- Time from raw log ingest to incident creation or update
- Percentage of incidents with useful AI summary attached
- Reduction in manual command hopping during investigation
- Approval-to-execution workflow completion rate

## Risks

- LLM latency may slow incident decision or summarization
- Log ingestion throughput may degrade without stronger indexing and queue visibility
- Heuristic parsing may classify noisy syslog imperfectly
- Operators may over-trust AI conclusions without enough evidence visibility

## Open Questions

- What is the target ingest volume for the lab in steady state and burst mode?
- Should the dashboard auto-refresh by default?
- Which incident classes should remain informational vs incident-worthy?
- How much of the remediation workflow should stay manual in the next phase?

## Milestones

### Phase 1

- Stable LLM-first chat workflow
- Safe CLI execution
- Inventory grounding
- Basic AIOps pages and ingest pipeline

### Phase 2

- Better log ingestion observability
- Faster incident correlation and queue processing
- Stronger empty states and dashboard visibility of logs
- Improved parser coverage for more syslog patterns

### Phase 3

- Better approval and verification workflows
- More operational analytics around incidents and evidence quality
- Stronger production-readiness hardening if the product moves beyond lab scope

## Current Product Positioning

This product should be positioned as an LLM-first network operations copilot with integrated incident-centric AIOps workflows for lab environments.
