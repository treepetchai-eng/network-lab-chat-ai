"""SSE adapter for the LLM-first free-run graph."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import suppress
from typing import Any, AsyncGenerator

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

logger = logging.getLogger(__name__)

from src.formatters import (
    extract_executed_command,
    fmt_cli_error,
    fmt_cli_output,
    fmt_device_list,
    fmt_lookup,
    is_error,
    parse_output,
)
from src.session_manager import SessionData

# ---------------------------------------------------------------------------
# Streaming cadence
# ---------------------------------------------------------------------------

_MIN_CHUNK = 8
_MID_CHUNK = 20
_MAX_CHUNK = 48
_SHORT_DELAY = 0.002
_MID_DELAY = 0.001
_LONG_DELAY = 0.0
_EXECUTION_TOOL_NAMES = {"run_cli", "run_diagnostic"}


# ---------------------------------------------------------------------------
# SSE event helpers
# ---------------------------------------------------------------------------


def _sse(event: str, **data: Any) -> dict:
    """Build an SSE-ready dict with *event* type and *data* payload."""
    return {"event": event, "data": data}


def _stream_cadence(text: str) -> tuple[int, float]:
    """Return chunk size and delay tuned for the final answer length.

    Short answers still stream with visible progress; longer answers avoid
    spending seconds in artificial token pacing after the model already
    finished.
    """
    length = len(text)
    if length < 180:
        return _MIN_CHUNK, _SHORT_DELAY
    if length < 600:
        return _MID_CHUNK, _MID_DELAY
    return _MAX_CHUNK, _LONG_DELAY


def _is_final_text_stream(namespace: Any, metadata: dict[str, Any] | None) -> bool:
    """Return True when a streamed message likely belongs to the final answer."""
    namespace_text = "/".join(str(part) for part in (namespace or ()))
    node_name = str((metadata or {}).get("langgraph_node", ""))
    return "free_run_agent" in namespace_text or node_name == "free_run_agent"


def _final_phase_status(msg: Any) -> str:
    """Map a final-answer message phase to a user-facing live status."""
    phase = str((getattr(msg, "additional_kwargs", {}) or {}).get("phase", "")).strip()
    if phase == "consistency_repair":
        return "Verifying counts and polishing final answer..."
    if phase == "topology_repair":
        return "Polishing topology answer..."
    if phase == "final_synthesis":
        return "Synthesizing final answer from evidence..."
    return "Summarizing answer..."


# ---------------------------------------------------------------------------
# Main streaming generator
# ---------------------------------------------------------------------------


async def stream_chat(
    session: SessionData, user_message: str
) -> AsyncGenerator[dict, None]:
    """Stream SSE events for a single user message.

    It wraps ``agent.astream()`` and yields frontend-friendly SSE payloads.
    """
    agent = session.graph
    config = {
        "configurable": {"thread_id": session.thread_id},
        "recursion_limit": 50,
    }
    inputs = {
        "messages": [
            *(
                list(getattr(session, "preloaded_messages", []))
                if getattr(session, "preloaded_messages", None)
                and not getattr(session, "preloaded_seeded", False)
                else []
            ),
            HumanMessage(content=user_message),
        ],
        "device_cache": session.device_cache,
        "incident_context": getattr(session, "incident_context", "") or "",
    }
    if getattr(session, "preloaded_messages", None) and not getattr(session, "preloaded_seeded", False):
        session.preloaded_seeded = True

    last_command = ""
    pending_commands: dict[str, dict[str, str]] = {}  # tool_call_id → metadata
    analyst_content = ""
    already_streamed = False  # True once analyst tokens have been sent live
    ssh_device_count = 0
    ssh_result_count = 0
    sent_reviewing_status = False
    sent_summarizing_status = False

    yield _sse(
        "status",
        text="Analyzing request...",
        tool_name=None,
        args=None,
    )

    progress_queue: asyncio.Queue[dict | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _progress_callback(event: dict) -> None:
        loop.call_soon_threadsafe(progress_queue.put_nowait, event)

    session.progress_sink["callback"] = _progress_callback

    graph_queue: asyncio.Queue[tuple[Any, str, Any] | None] = asyncio.Queue()

    async def _consume_graph_stream() -> None:
        try:
            async for namespace, stream_type, data in agent.astream(
                inputs, config=config, stream_mode=["messages"],
                subgraphs=True,
            ):
                await graph_queue.put((namespace, stream_type, data))
        except Exception as exc:
            await graph_queue.put(((), "__graph_error__", exc))
        finally:
            await graph_queue.put(None)

    consumer_task: asyncio.Task | None = None

    try:
        consumer_task = asyncio.create_task(_consume_graph_stream())
        graph_done = False

        while not graph_done:
            while not progress_queue.empty():
                progress_event = await progress_queue.get()
                if progress_event and progress_event.get("kind") == "status":
                    yield _sse(
                        "status",
                        text=progress_event.get("text", ""),
                        tool_name=progress_event.get("tool_name"),
                        args=progress_event.get("args"),
                    )

            try:
                item = await asyncio.wait_for(graph_queue.get(), timeout=0.05)
            except asyncio.TimeoutError:
                continue

            if item is None:
                graph_done = True
                continue

            namespace, stream_type, data = item

            if stream_type == "__graph_error__":
                raise data

            if stream_type == "messages":
                msg, _metadata = data

                # AIMessage with tool_calls → status update
                if isinstance(msg, AIMessage) and getattr(
                    msg, "tool_calls", None
                ):
                    execution_calls = [
                        tc for tc in msg.tool_calls
                        if tc.get("name", "") in _EXECUTION_TOOL_NAMES
                    ]
                    if execution_calls:
                        for tc in execution_calls:
                            args = tc.get("args", {})
                            if tc.get("name") == "run_diagnostic":
                                diag_kind = str(args.get("kind", "") or "").strip().lower()
                                diag_target = str(args.get("target", "") or "").strip()
                                last_command = f"{diag_kind} {diag_target}".strip() or "diagnostic"
                            else:
                                last_command = args.get("command", "?")
                            host = args.get("host", "")
                            call_id = tc.get("id", "")
                            if call_id:
                                pending_commands[call_id] = {
                                    "command": last_command,
                                    "host": host,
                                }
                        ssh_device_count += len(execution_calls)
                        if len(execution_calls) > 1:
                            command_set = {
                                (
                                    f"{str(tc.get('args', {}).get('kind', '') or '').strip().lower()} "
                                    f"{str(tc.get('args', {}).get('target', '') or '').strip()}".strip()
                                    if tc.get("name") == "run_diagnostic"
                                    else str(tc.get("args", {}).get("command", "") or "").strip()
                                )
                                for tc in execution_calls
                            }
                            if len(command_set) == 1:
                                batch_command = next(iter(command_set)) or "CLI checks"
                                text = (
                                    f"Running `{batch_command}` on "
                                    f"{len(execution_calls)} devices in parallel ..."
                                )
                            else:
                                text = (
                                    f"Running {len(execution_calls)} execution checks "
                                    "in parallel ..."
                                )
                            yield _sse(
                                "status",
                                text=text,
                                tool_name="run_diagnostic",
                                args={"current": 0, "total": len(execution_calls)},
                            )

                    for tc in msg.tool_calls:
                        name = tc.get("name", "")
                        args = tc.get("args", {})

                        if name == "lookup_device":
                            hostname = args.get("hostname", "?")
                            yield _sse(
                                "status",
                                text=f"Looking up {hostname} ...",
                                tool_name="lookup_device",
                                args=args,
                            )

                        elif name == "list_all_devices":
                            yield _sse(
                                "status",
                                text="Loading full device inventory ...",
                                tool_name="list_all_devices",
                                args=args,
                            )

                        elif name in _EXECUTION_TOOL_NAMES:
                            if len(execution_calls) <= 1:
                                if name == "run_diagnostic":
                                    last_command = (
                                        f"{str(args.get('kind', '') or '').strip().lower()} "
                                        f"{str(args.get('target', '') or '').strip()}"
                                    ).strip() or "diagnostic"
                                else:
                                    last_command = args.get("command", "?")
                                host = args.get("host", "")
                                text = (
                                    f"Running `{last_command}`"
                                    f" on {host} ..."
                                )
                                yield _sse(
                                    "status",
                                    text=text,
                                    tool_name=name,
                                    args={"current": 0, "total": 1, **args},
                                )

                # ToolMessage → formatted tool result
                elif isinstance(msg, ToolMessage):
                    tool_name = getattr(msg, "name", "tool")
                    raw = (
                        msg.content
                        if isinstance(msg.content, str)
                        else str(msg.content)
                    )
                    if not sent_reviewing_status:
                        yield _sse(
                            "status",
                            text="Reviewing results...",
                            tool_name=None,
                            args=None,
                        )
                        sent_reviewing_status = True

                    if tool_name in _EXECUTION_TOOL_NAMES:
                        ssh_result_count += 1
                        hostname, ip, os_type, body = parse_output(raw)
                        is_err = is_error(body)
                        # Resolve command from pending_commands map (batch-safe)
                        call_id = getattr(msg, "tool_call_id", "")
                        pending = pending_commands.pop(call_id, {})
                        cmd = extract_executed_command(raw) or pending.get("command", last_command)
                        pending_host = pending.get("host", "")
                        if not hostname and pending_host:
                            hostname = pending_host
                        if not ip and hostname in session.device_cache:
                            ip = session.device_cache[hostname].get("ip_address", "")
                        if not os_type and hostname in session.device_cache:
                            os_type = session.device_cache[hostname].get("os_platform", "")
                        step_name = (
                            f"FAILED \u2014 {cmd} @ {hostname or pending_host or '?'}"
                            if is_err
                            else f"{cmd} @ {hostname}"
                        )
                        step_output = (
                            fmt_cli_error(ip, body)
                            if is_err
                            else fmt_cli_output(
                                hostname, ip, os_type, cmd, body
                            )
                        )
                        yield _sse(
                            "tool_result",
                            tool_name=tool_name,
                            step_name=step_name,
                            content=step_output,
                            is_error=is_err,
                            raw=raw,
                        )
                        if ssh_device_count > 1 and ssh_result_count < ssh_device_count:
                            yield _sse(
                                "status",
                                text=(
                                    f"Collected {ssh_result_count}/{ssh_device_count} "
                                    "execution results ..."
                                ),
                                tool_name=tool_name,
                                args={"current": ssh_result_count, "total": ssh_device_count},
                            )

                    elif tool_name == "lookup_device":
                        try:
                            parsed = json.loads(raw)
                            found = "error" not in parsed
                        except Exception:
                            parsed = {}
                            found = False

                        # Update session device cache
                        if found:
                            session.device_cache[parsed["hostname"]] = {
                                "ip_address": parsed.get("ip_address", ""),
                                "os_platform": parsed.get("os_platform", ""),
                                "device_role": parsed.get("device_role", ""),
                                "site": parsed.get("site", ""),
                            }

                        resolved_via = parsed.get("resolved_via", "")
                        step_name = (
                            (
                                f"Found {parsed.get('hostname', '?')} via interface IP"
                                if found and resolved_via == "interface_ip"
                                else f"Found {parsed.get('hostname', '?')}"
                            )
                            if found
                            else "Device not found"
                        )
                        yield _sse(
                            "tool_result",
                            tool_name="lookup_device",
                            step_name=step_name,
                            content=fmt_lookup(raw),
                            is_error=not found,
                            raw=raw,
                        )

                    elif tool_name == "list_all_devices":
                        yield _sse(
                            "tool_result",
                            tool_name="list_all_devices",
                            step_name="Inventory",
                            content=fmt_device_list(raw),
                            is_error=False,
                            raw=raw,
                        )

                elif (
                    isinstance(msg, (AIMessage, AIMessageChunk))
                    and not getattr(msg, "tool_calls", None)
                ):
                    content = (msg.content or "").strip()

                    if (
                        content
                        and _is_final_text_stream(namespace, _metadata)
                    ):
                        content = re.sub(
                            r"<think>.*?</think>",
                            "",
                            content,
                            flags=re.DOTALL,
                        ).strip()
                        if content:
                            analyst_content += content
                            already_streamed = True
                            if not sent_summarizing_status:
                                yield _sse(
                                    "status",
                                    text=_final_phase_status(msg),
                                    tool_name=None,
                                    args=None,
                                )
                                sent_summarizing_status = True
                            yield _sse("analyst_token", token=content)
                            continue

        if consumer_task is not None:
            await consumer_task

    except Exception as exc:
        logger.error("stream_chat: graph error mid-stream: %s", exc, exc_info=True)
        yield _sse("error", message=str(exc), type="graph_error")
        # Fall through to the recovery logic below instead of returning early,
        # so callers (e.g. incident troubleshooting) still receive analyst_done
        # with whatever partial content was collected.
    finally:
        if consumer_task is not None and not consumer_task.done():
            consumer_task.cancel()
            with suppress(asyncio.CancelledError):
                await consumer_task
        session.progress_sink["callback"] = None

    if not analyst_content:
        try:
            final_state = await agent.aget_state(config)
            for m in reversed(final_state.values.get("messages", [])):
                if (
                    hasattr(m, "content")
                    and m.content
                    and not getattr(m, "tool_calls", None)
                ):
                    content = re.sub(
                        r"<think>.*?</think>",
                        "",
                        m.content.strip(),
                        flags=re.DOTALL,
                    ).strip()
                    if content and len(content) > 5:
                        analyst_content = content
                        break
        except Exception:
            pass

    if not analyst_content:
        analyst_content = "ไม่สามารถประมวลผลได้ กรุณาลองถามใหม่อีกครั้ง"

    # If we already streamed analyst tokens live, skip re-streaming and just
    # send the done event with the full content.
    if not sent_summarizing_status:
        yield _sse(
            "status",
            text="Summarizing answer...",
            tool_name=None,
            args=None,
        )

    if not already_streamed:
        chunk_size, delay = _stream_cadence(analyst_content)
        for i in range(0, len(analyst_content), chunk_size):
            yield _sse(
                "analyst_token",
                token=analyst_content[i : i + chunk_size],
            )
            if delay > 0 and i + chunk_size < len(analyst_content):
                await asyncio.sleep(delay)

    yield _sse("analyst_done", full_content=analyst_content)
    yield _sse("done")
