"""Minimal shared state for the LLM-first free-run graph."""

from __future__ import annotations

import operator
from typing import Annotated, Sequence

from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict


class AgentState(TypedDict):
    """Shared state flowing through the free-run graph."""

    messages: Annotated[Sequence[BaseMessage], operator.add]
    device_cache: dict
