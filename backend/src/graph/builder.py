"""Build the LLM-first free-run graph used by the backend."""

from __future__ import annotations

from functools import partial

from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from src.graph.agents.free_run_agent import free_run_node
from src.graph.state import AgentState
from src.llm_factory import create_chat_model
from src.tools.cli_tool import create_run_cli_tool

load_dotenv()


def _create_llm(reasoning: bool = False):
    """Create the configured chat model instance.

    Parameters
    ----------
    reasoning:
        Enable provider-specific settings for pure-text synthesis nodes.

    Performance notes
    -----------------
    - Ollama keeps the existing `num_ctx` / `num_predict` tuning.
    - API providers use the same prompt and evidence flow, with optional
      `LLM_MAX_TOKENS` / `LLM_ANSWER_MAX_TOKENS` limits.
    """
    load_dotenv(override=True)
    return create_chat_model(reasoning=reasoning)


def build_graph(device_cache: dict | None = None, progress_sink: dict | None = None):
    """Build and return the compiled LLM-first free-run graph."""
    llm = _create_llm(reasoning=False)
    answer_llm = _create_llm(reasoning=True)

    if device_cache is None:
        device_cache = {}
    run_cli_tool = create_run_cli_tool(device_cache)

    graph = StateGraph(AgentState)
    graph.add_node(
        "free_run_agent",
        partial(
            free_run_node,
            llm=llm,
            answer_llm=answer_llm,
            progress_sink=progress_sink or {},
            run_cli_tool=run_cli_tool,
        ),
    )
    graph.set_entry_point("free_run_agent")
    graph.add_edge("free_run_agent", END)

    compiled = graph.compile(checkpointer=MemorySaver())
    return compiled
