"""Agentic Test Planner — parses PRD JSON and generates a structured test matrix.

This module simulates what a Claude-backed agentic planner would do:
  1. Load the product requirements document
  2. Parse each requirement's rules via a registered rule-parser
  3. Derive test cases covering: happy path, negative, bulk, and meta verification
  4. Emit a test_plan.json file consumed by the pytest suite

In a production system the per-requirement parsers would be replaced by LLM
tool-calling with schema-validated JSON output. The interface stays identical.

Copyright 2024 Agentic Test Engine Project.
All Rights Reserved. Confidential.
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List

PRD_PATH = Path(__file__).parent.parent / "prd.json"
OUTPUT_PATH = Path(__file__).parent.parent / "tests" / "generated_test_plan.json"


# ---------------------------------------------------------------------------
# Rule parsers — one per req_id; each returns a list of test-case dicts.
# In an LLM-backed system these are replaced with prompted tool calls.
# ---------------------------------------------------------------------------

def _parse_req_001(req: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Generate test cases for REQ-001: CRITICAL + GCP → Slack within 2 s."""
    return [
        {
            "test_id": "REQ-001-TC-001",
            "name": "CRITICAL GCP alert routed to Slack within 2-second SLA",
            "req_id": "REQ-001",
            "category": "happy_path",
            "method": "POST",
            "endpoint": "/api/v1/alerts",
            "payload": {
                "severity": "CRITICAL",
                "source": "GCP",
                "message": "Unauthorized SSH access detected on prod-gke-node-01",
                "alert_id": str(uuid.uuid4()),
            },
            "assertions": [
                {"field": "status", "op": "eq", "value": "routed"},
                {"field": "channel", "op": "eq", "value": "#security-gcp-critical"},
                {"field": "within_sla", "op": "eq", "value": True},
                {"field": "elapsed_seconds", "op": "lt", "value": 2.0},
            ],
            "verify_slack": True,
            "expected_slack_channel": "#security-gcp-critical",
        },
        {
            "test_id": "REQ-001-TC-002",
            "name": "CRITICAL AWS alert does NOT route to GCP Slack channel",
            "req_id": "REQ-001",
            "category": "negative",
            "method": "POST",
            "endpoint": "/api/v1/alerts",
            "payload": {
                "severity": "CRITICAL",
                "source": "AWS",
                "message": "Root access detected on ec2-prod-bastion",
                "alert_id": str(uuid.uuid4()),
            },
            "assertions": [
                {"field": "status", "op": "ne", "value": "routed"},
            ],
            "verify_slack": False,
            "expected_slack_channel": None,
        },
        {
            "test_id": "REQ-001-TC-003",
            "name": "HIGH severity GCP alert is NOT treated as CRITICAL routing",
            "req_id": "REQ-001",
            "category": "negative",
            "method": "POST",
            "endpoint": "/api/v1/alerts",
            "payload": {
                "severity": "HIGH",
                "source": "GCP",
                "message": "API error rate elevated on gke-prod-cluster",
                "alert_id": str(uuid.uuid4()),
            },
            "assertions": [
                {"field": "status", "op": "ne", "value": "routed"},
            ],
            "verify_slack": False,
            "expected_slack_channel": None,
        },
        {
            "test_id": "REQ-001-TC-004",
            "name": "CRITICAL GCP response body references correct req_id",
            "req_id": "REQ-001",
            "category": "meta",
            "method": "POST",
            "endpoint": "/api/v1/alerts",
            "payload": {
                "severity": "CRITICAL",
                "source": "GCP",
                "message": "Credential stuffing detected on GCP IAM",
                "alert_id": str(uuid.uuid4()),
            },
            "assertions": [
                {"field": "req_id", "op": "eq", "value": "REQ-001"},
                {"field": "status", "op": "eq", "value": "routed"},
            ],
            "verify_slack": True,
            "expected_slack_channel": "#security-gcp-critical",
        },
    ]


def _parse_req_002(req: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Generate test cases for REQ-002: LOW → DB batch, no webhook."""
    return [
        {
            "test_id": "REQ-002-TC-001",
            "name": "LOW severity GCP alert batched in DB, no Slack webhook",
            "req_id": "REQ-002",
            "category": "happy_path",
            "method": "POST",
            "endpoint": "/api/v1/alerts",
            "payload": {
                "severity": "LOW",
                "source": "GCP",
                "message": "Minor config drift on gke-dev-cluster",
                "alert_id": str(uuid.uuid4()),
            },
            "assertions": [
                {"field": "status", "op": "eq", "value": "batched"},
                {"field": "webhook_triggered", "op": "eq", "value": False},
                {"field": "storage", "op": "eq", "value": "database"},
            ],
            "verify_no_slack": True,
        },
        {
            "test_id": "REQ-002-TC-002",
            "name": "LOW severity AWS alert batched in DB, no Slack webhook",
            "req_id": "REQ-002",
            "category": "happy_path",
            "method": "POST",
            "endpoint": "/api/v1/alerts",
            "payload": {
                "severity": "LOW",
                "source": "AWS",
                "message": "Disk usage at 65% on ec2-staging-node-02",
                "alert_id": str(uuid.uuid4()),
            },
            "assertions": [
                {"field": "status", "op": "eq", "value": "batched"},
                {"field": "webhook_triggered", "op": "eq", "value": False},
                {"field": "storage", "op": "eq", "value": "database"},
            ],
            "verify_no_slack": True,
        },
        {
            "test_id": "REQ-002-TC-003",
            "name": "Bulk 5x LOW alerts — all persisted in DB, no webhooks",
            "req_id": "REQ-002",
            "category": "bulk",
            "method": "POST",
            "endpoint": "/api/v1/alerts",
            "payloads": [
                {
                    "severity": "LOW",
                    "source": "GCP",
                    "message": f"Batch low-priority event {i}",
                    "alert_id": str(uuid.uuid4()),
                }
                for i in range(5)
            ],
            "assertions": [
                {"field": "status", "op": "eq", "value": "batched"},
            ],
            "verify_no_slack": True,
            "bulk_count": 5,
        },
        {
            "test_id": "REQ-002-TC-004",
            "name": "LOW alert response body references correct req_id",
            "req_id": "REQ-002",
            "category": "meta",
            "method": "POST",
            "endpoint": "/api/v1/alerts",
            "payload": {
                "severity": "LOW",
                "source": "Azure",
                "message": "Scheduled backup completed successfully",
                "alert_id": str(uuid.uuid4()),
            },
            "assertions": [
                {"field": "req_id", "op": "eq", "value": "REQ-002"},
                {"field": "status", "op": "eq", "value": "batched"},
            ],
            "verify_no_slack": True,
        },
    ]


# Registry: maps req_id → parser function
_PARSERS: Dict[str, Callable[[Dict[str, Any]], List[Dict[str, Any]]]] = {
    "REQ-001": _parse_req_001,
    "REQ-002": _parse_req_002,
}


def generate_test_plan(
    prd_path: Path = PRD_PATH,
    output_path: Path = OUTPUT_PATH,
) -> Dict[str, Any]:
    """Parse the PRD and emit a structured test plan JSON.

    args:
        :prd_path: Path to the product requirements JSON file
        :output_path: Destination path for the generated test plan

    returns:
        dict containing the full test plan with generated test cases and summary
    """
    with open(prd_path) as fh:
        prd = json.load(fh)

    test_cases: List[Dict[str, Any]] = []
    for req in prd.get("requirements", []):
        parser = _PARSERS.get(req["req_id"])
        if parser:
            test_cases.extend(parser(req))
        else:
            print(f"[planner] No parser registered for {req['req_id']} — skipped")

    categories = ("happy_path", "negative", "bulk", "meta")
    plan: Dict[str, Any] = {
        "feature": prd["feature_name"],
        "description": prd["description"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_tests": len(test_cases),
        "test_cases": test_cases,
        "summary": {
            "by_req": {
                req_id: len([tc for tc in test_cases if tc["req_id"] == req_id])
                for req_id in _PARSERS
            },
            "by_category": {
                cat: len([tc for tc in test_cases if tc.get("category") == cat])
                for cat in categories
            },
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as fh:
        json.dump(plan, fh, indent=2)

    print(f"[planner] Generated {len(test_cases)} test cases → {output_path}")
    return plan


if __name__ == "__main__":
    plan = generate_test_plan()
    print(json.dumps(plan["summary"], indent=2))
