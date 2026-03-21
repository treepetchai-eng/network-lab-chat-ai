from __future__ import annotations

import sys
from pathlib import Path

from langchain_core.messages import AIMessage

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))


def test_final_phase_status_for_synthesis():
    from src.sse_stream import _final_phase_status

    msg = AIMessage(content="done", additional_kwargs={"phase": "final_synthesis"})
    assert _final_phase_status(msg) == "Synthesizing final answer from evidence..."


def test_final_phase_status_for_consistency_repair():
    from src.sse_stream import _final_phase_status

    msg = AIMessage(content="done", additional_kwargs={"phase": "consistency_repair"})
    assert _final_phase_status(msg) == "Verifying counts and polishing final answer..."


def test_final_phase_status_for_topology_repair():
    from src.sse_stream import _final_phase_status

    msg = AIMessage(content="done", additional_kwargs={"phase": "topology_repair"})
    assert _final_phase_status(msg) == "Polishing topology answer..."
