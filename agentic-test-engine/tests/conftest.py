"""Pytest configuration: shared fixtures and dynamically-loaded test parametrization.

The MCP health-check fixture gates the entire test session — no tests run unless
the infrastructure bridge reports ready_for_testing=true. This mirrors how a real
agentic system would validate environment state before dispatching test jobs.

Parametrize sets (REQ001_CASES, REQ002_*) are derived from the generated test plan
so the planning artifact directly drives test execution.

Copyright 2024 Agentic Test Engine Project.
All Rights Reserved. Confidential.
"""
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import httpx
import pytest

ALERT_API_URL = os.getenv("ALERT_API_URL", "http://localhost:8000")
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8001")
PLAN_PATH = Path(__file__).parent / "generated_test_plan.json"


# ---------------------------------------------------------------------------
# Load (or auto-generate) the test plan at collection time
# ---------------------------------------------------------------------------

def _load_or_generate_plan() -> Dict[str, Any]:
    if not PLAN_PATH.exists():
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from planner.test_planner import generate_test_plan
        return generate_test_plan()
    with open(PLAN_PATH) as fh:
        return json.load(fh)


TEST_PLAN = _load_or_generate_plan()


def _cases(req_id: str, category: str = None) -> List[pytest.param]:
    """Build pytest.param list from the generated plan, optionally filtered by category."""
    cases = [tc for tc in TEST_PLAN["test_cases"] if tc["req_id"] == req_id]
    if category:
        cases = [tc for tc in cases if tc.get("category") == category]
    return [pytest.param(tc, id=tc["test_id"]) for tc in cases]


# Parametrize sets consumed by test_alerts.py
REQ001_CASES = _cases("REQ-001")
REQ002_SINGLE_CASES = [p for p in _cases("REQ-002") if p.values[0].get("category") != "bulk"]
REQ002_BULK_CASES = _cases("REQ-002", category="bulk")


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def verify_system_health() -> Dict[str, Any]:
    """MCP infrastructure bridge: verify environment readiness before any test runs.

    Retries for up to 36 seconds (12 x 3 s) to accommodate Docker startup latency.
    Calls pytest.fail (not skip) so CI pipelines report the failure explicitly.
    """
    max_retries = 12
    delay_s = 3

    for attempt in range(1, max_retries + 1):
        try:
            resp = httpx.get(f"{MCP_SERVER_URL}/system/status", timeout=5)
            data = resp.json()
            if data.get("ready_for_testing"):
                print(f"\n[MCP] System ready (attempt {attempt}) — services={data['services']}")
                return data
        except Exception as exc:
            print(f"\n[MCP] Attempt {attempt}/{max_retries}: {exc}")

        if attempt < max_retries:
            time.sleep(delay_s)

    pytest.fail(
        f"MCP system status check failed after {max_retries} attempts. "
        f"Ensure docker-compose is running: docker-compose up -d"
    )


@pytest.fixture(scope="session")
def alert_client() -> httpx.Client:
    with httpx.Client(base_url=ALERT_API_URL, timeout=10) as client:
        yield client


@pytest.fixture(scope="session")
def mcp_client() -> httpx.Client:
    with httpx.Client(base_url=MCP_SERVER_URL, timeout=10) as client:
        yield client
