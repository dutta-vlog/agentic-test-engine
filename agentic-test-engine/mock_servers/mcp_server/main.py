"""MCP System Status Server — simulates the Model Context Protocol infrastructure bridge.

Exposes:
  GET    /health                — liveness probe
  GET    /system/status         — environment readiness (gates test execution)
  POST   /mock/slack            — capture outbound Slack webhook calls
  GET    /mock/slack/messages   — inspect captured Slack messages (filter by alert_id)
  DELETE /mock/slack/messages   — reset Slack message store between runs

Copyright 2024 Agentic Test Engine Project.
All Rights Reserved. Confidential.
"""
from datetime import datetime, timezone
from typing import Any, List, Optional

from fastapi import FastAPI

app = FastAPI(title="MCP System Status Server", version="1.0.0")

# In-memory Slack message capture (reset between test runs via DELETE endpoint)
_slack_messages: List[dict] = []


@app.get("/health")
def health() -> dict:
    return {
        "status": "healthy",
        "service": "mcp-server",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/system/status")
def system_status() -> dict:
    """Primary MCP bridge endpoint — called by the test orchestrator before dispatching any tests."""
    return {
        "status": "operational",
        "environment": "test",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ready_for_testing": True,
        "services": {
            "alert_api": "up",
            "database": "up",
            "slack_integration": "up",
        },
    }


@app.post("/mock/slack")
async def mock_slack_webhook(payload: dict) -> dict:
    """Capture a Slack webhook call. Records the full payload for test assertions."""
    _slack_messages.append(
        {
            "received_at": datetime.now(timezone.utc).isoformat(),
            "channel": payload.get("channel"),
            "text": payload.get("text"),
            "alert_id": payload.get("alert_id"),
            "severity": payload.get("severity"),
            "source": payload.get("source"),
        }
    )
    return {"ok": True}


@app.get("/mock/slack/messages")
def get_slack_messages(alert_id: Optional[str] = None) -> dict:
    """Return captured Slack messages, optionally filtered by alert_id for parallel-safe assertions."""
    msgs = (
        [m for m in _slack_messages if m.get("alert_id") == alert_id]
        if alert_id
        else list(_slack_messages)
    )
    return {"messages": msgs, "count": len(msgs)}


@app.delete("/mock/slack/messages")
def clear_slack_messages() -> dict:
    """Reset the Slack message store. Called between full test runs, not between individual tests."""
    _slack_messages.clear()
    return {"status": "cleared"}
