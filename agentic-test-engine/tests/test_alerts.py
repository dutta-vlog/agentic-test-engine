"""Alert routing tests — driven entirely by the generated test plan.

Each test function consumes parametrized cases produced by planner/test_planner.py.
New requirements automatically produce new test cases without touching this file.

Parallelism: run with `pytest -n auto` (pytest-xdist).
Thread-safety: each test case uses a fresh UUID alert_id; Slack assertions filter
by that ID, so workers never see each other's messages.

Copyright 2024 Agentic Test Engine Project.
All Rights Reserved. Confidential.
"""
import uuid
from typing import Any, Dict

import pytest

from tests.conftest import REQ001_CASES, REQ002_BULK_CASES, REQ002_SINGLE_CASES

# ---------------------------------------------------------------------------
# Assertion engine — evaluates the structured assertion rules from the plan
# ---------------------------------------------------------------------------

_OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "in": lambda a, b: a in b,
}


def _assert_response(body: Dict[str, Any], assertions: list) -> None:
    """Evaluate all assertion rules against a response body dict."""
    for rule in assertions:
        field = rule["field"]
        op = rule.get("op", "eq")
        expected = rule["value"]
        actual = body.get(field)
        assert _OPS[op](actual, expected), (
            f"Assertion failed — field={field!r} op={op!r} "
            f"expected={expected!r} got={actual!r}"
        )


# ---------------------------------------------------------------------------
# REQ-001: CRITICAL + GCP → Slack #security-gcp-critical within 2 s
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tc", REQ001_CASES)
def test_req001_alert_routing(tc: Dict, alert_client, mcp_client) -> None:
    """Drive each REQ-001 case: routing status, SLA, and Slack capture assertions."""
    # Assign a fresh alert_id so parallel workers can filter independently
    payload = {**tc["payload"], "alert_id": str(uuid.uuid4())}

    resp = alert_client.post(tc["endpoint"], json=payload)
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"

    _assert_response(resp.json(), tc["assertions"])

    if tc.get("verify_slack"):
        slack = mcp_client.get("/mock/slack/messages", params={"alert_id": payload["alert_id"]})
        msgs = slack.json()["messages"]
        assert len(msgs) >= 1, (
            f"Expected Slack notification for alert_id={payload['alert_id']} but got none"
        )
        assert msgs[0]["channel"] == tc["expected_slack_channel"], (
            f"Wrong Slack channel: got {msgs[0]['channel']!r}, "
            f"expected {tc['expected_slack_channel']!r}"
        )

    # verify_slack=False means the alert must NOT produce a Slack message
    if tc.get("verify_slack") is False:
        slack = mcp_client.get("/mock/slack/messages", params={"alert_id": payload["alert_id"]})
        msgs = slack.json()["messages"]
        assert len(msgs) == 0, (
            f"Expected no Slack message for alert_id={payload['alert_id']} but got: {msgs}"
        )


# ---------------------------------------------------------------------------
# REQ-002: LOW → batch in DB, no real-time webhook
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tc", REQ002_SINGLE_CASES)
def test_req002_low_alert_batching(tc: Dict, alert_client, mcp_client) -> None:
    """Drive each single-payload REQ-002 case: DB persistence and no-webhook assertions."""
    payload = {**tc["payload"], "alert_id": str(uuid.uuid4())}

    resp = alert_client.post(tc["endpoint"], json=payload)
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"

    _assert_response(resp.json(), tc["assertions"])

    if tc.get("verify_no_slack"):
        slack = mcp_client.get("/mock/slack/messages", params={"alert_id": payload["alert_id"]})
        assert slack.json()["count"] == 0, (
            f"LOW alert must not trigger a Slack webhook — "
            f"got messages: {slack.json()['messages']}"
        )

    # Confirm the alert was actually persisted in the DB
    db_resp = alert_client.get("/api/v1/alerts/batched", params={"alert_id": payload["alert_id"]})
    assert db_resp.json()["count"] == 1, (
        f"Alert {payload['alert_id']} should be stored in the database"
    )


@pytest.mark.parametrize("tc", REQ002_BULK_CASES)
def test_req002_bulk_batching(tc: Dict, alert_client, mcp_client) -> None:
    """Verify bulk LOW alerts are all persisted and none trigger Slack webhooks."""
    # Assign fresh unique IDs to each payload so parallel runs don't collide
    payloads = [{**p, "alert_id": str(uuid.uuid4())} for p in tc["payloads"]]

    for payload in payloads:
        resp = alert_client.post(tc["endpoint"], json=payload)
        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        _assert_response(resp.json(), tc["assertions"])

    for payload in payloads:
        # Each alert must be individually findable in the DB
        db_resp = alert_client.get("/api/v1/alerts/batched", params={"alert_id": payload["alert_id"]})
        assert db_resp.json()["count"] == 1, (
            f"Bulk alert {payload['alert_id']} not found in database"
        )

        # And must NOT have fired a Slack webhook
        slack = mcp_client.get("/mock/slack/messages", params={"alert_id": payload["alert_id"]})
        assert slack.json()["count"] == 0, (
            f"LOW bulk alert {payload['alert_id']} unexpectedly triggered Slack"
        )
