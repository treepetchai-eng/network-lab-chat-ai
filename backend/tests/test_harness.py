"""Tests for the free-run E2E harness evaluation logic."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from tests.e2e_test_harness import (
    DEFAULT_MOCK_SCENARIOS,
    Scenario,
    evaluate_run,
    run_all_suites,
    run_mock_scenario_suite,
)


def test_evaluate_run_scores_strong_free_run_result_highly():
    scenario = Scenario(
        name="demo",
        prompts=["show bgp summary on HQ-CORE-RT01"],
        expected_hosts=["HQ-CORE-RT01"],
        expected_step_keywords=["show ip bgp summary @ HQ-CORE-RT01"],
        answer_keywords=["100.66.0.2", "hq-core-rt01"],
        min_tool_results=2,
        require_run_cli=True,
    )
    results = [{
        "tool_results": [
            {"tool_name": "lookup_device", "step_name": "Found HQ-CORE-RT01"},
            {"tool_name": "run_cli", "step_name": "show ip bgp summary @ HQ-CORE-RT01"},
        ],
        "analyst_content": "BGP on HQ-CORE-RT01 is healthy with established peer 100.66.0.2.",
        "error": None,
    }]
    report = evaluate_run(results, scenario)
    assert report["passed"] is True
    assert report["average"] >= 8.0


def test_evaluate_run_penalizes_unsafe_command():
    scenario = Scenario(name="unsafe", prompts=["unsafe"], require_run_cli=True)
    results = [{
        "tool_results": [{"tool_name": "run_cli", "step_name": "configure terminal @ HQ-CORE-RT01"}],
        "analyst_content": "done",
        "error": None,
    }]
    report = evaluate_run(results, scenario)
    assert report["scores"]["safety_compliance"] == 0
    assert report["passed"] is False


def test_evaluate_run_penalizes_inventory_only_reachability_claim():
    scenario = Scenario(
        name="inventory_only",
        prompts=["ขอรายชื่ออุปกรณ์ทั้งหมดหน่อย"],
        answer_keywords=["9"],
        forbidden_answer_keywords=["reachable"],
        min_tool_results=1,
        max_tool_results=1,
        require_no_run_cli=True,
    )
    results = [{
        "tool_results": [{"tool_name": "list_all_devices", "step_name": "Inventory"}],
        "analyst_content": "พบ 9 อุปกรณ์และทุกตัว reachable",
        "error": None,
    }]
    report = evaluate_run(results, scenario)
    assert report["scores"]["final_answer_accuracy"] == 3
    assert report["passed"] is False


def test_run_mock_scenario_suite_reports_non_real_cases():
    reports = run_mock_scenario_suite()
    assert len(reports) == len(DEFAULT_MOCK_SCENARIOS)
    assert all(report["real_env"] is False for report in reports)
    assert all(report["passed"] is True for report in reports)


def test_run_all_suites_keeps_real_and_mock_separate(monkeypatch):
    monkeypatch.setattr("tests.e2e_test_harness.run_scenario_suite", lambda: [{"scenario": "real", "real_env": True}])
    monkeypatch.setattr("tests.e2e_test_harness.run_mock_scenario_suite", lambda: [{"scenario": "mock", "real_env": False}])
    all_reports = run_all_suites()
    assert list(all_reports.keys()) == ["real", "mock"]
    assert all_reports["real"][0]["real_env"] is True
    assert all_reports["mock"][0]["real_env"] is False
