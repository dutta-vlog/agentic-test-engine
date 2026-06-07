"""Alert Routing API — mock microservice for the AI-Driven Intelligent Alerting Engine.

Implements:
  POST /api/v1/alerts  — routes CRITICAL+GCP to Slack, batches LOW to SQLite
  GET  /api/v1/alerts/batched — query the batch store
  GET  /health

Copyright 2024 Agentic Test Engine Project.
All Rights Reserved. Confidential.
"""
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

DB_PATH = os.getenv("DB_PATH", "/data/alerts.db")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "http://localhost:8001/mock/slack")


def _init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS batched_alerts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id   TEXT,
                severity   TEXT NOT NULL,
                source     TEXT NOT NULL,
                message    TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_db()
    yield


app = FastAPI(title="Alert Routing API", version="1.0.0", lifespan=lifespan)


class AlertPayload(BaseModel):
    severity: str
    source: str
    message: str
    alert_id: Optional[str] = None


@app.get("/health")
def health() -> dict:
    return {
        "status": "healthy",
        "service": "alert-api",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/v1/alerts")
async def create_alert(alert: AlertPayload) -> dict:
    start = time.monotonic()

    # REQ-001: CRITICAL + GCP → Slack #security-gcp-critical within 2 s
    if alert.severity == "CRITICAL" and alert.source == "GCP":
        async with httpx.AsyncClient(timeout=1.8) as client:
            try:
                await client.post(
                    SLACK_WEBHOOK_URL,
                    json={
                        "channel": "#security-gcp-critical",
                        "text": f"[CRITICAL][GCP] {alert.message}",
                        "alert_id": alert.alert_id,
                        "severity": alert.severity,
                        "source": alert.source,
                    },
                )
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"Slack routing failed: {exc}") from exc

        elapsed = time.monotonic() - start
        return {
            "status": "routed",
            "req_id": "REQ-001",
            "channel": "#security-gcp-critical",
            "elapsed_seconds": round(elapsed, 4),
            "within_sla": elapsed < 2.0,
        }

    # REQ-002: LOW → batch in DB, no real-time webhook
    if alert.severity == "LOW":
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO batched_alerts (alert_id, severity, source, message, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    alert.alert_id,
                    alert.severity,
                    alert.source,
                    alert.message,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()

        return {
            "status": "batched",
            "req_id": "REQ-002",
            "webhook_triggered": False,
            "storage": "database",
        }

    # All other combos — acknowledged, no routing rule matched
    return {
        "status": "received",
        "req_id": None,
        "action": "no_routing_rule_matched",
    }


@app.get("/api/v1/alerts/batched")
def get_batched_alerts(alert_id: Optional[str] = None) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        if alert_id:
            cursor = conn.execute(
                "SELECT id, alert_id, severity, source, message, created_at "
                "FROM batched_alerts WHERE alert_id = ?",
                (alert_id,),
            )
        else:
            cursor = conn.execute(
                "SELECT id, alert_id, severity, source, message, created_at "
                "FROM batched_alerts ORDER BY created_at DESC"
            )
        rows = cursor.fetchall()

    alerts = [
        {
            "id": r[0],
            "alert_id": r[1],
            "severity": r[2],
            "source": r[3],
            "message": r[4],
            "created_at": r[5],
        }
        for r in rows
    ]
    return {"alerts": alerts, "count": len(alerts)}
