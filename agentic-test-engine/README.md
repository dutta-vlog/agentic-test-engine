# Agentic Test Automation Engine

A mini-framework that reads a Product Requirements Document, generates a structured
test matrix, verifies infrastructure health via an MCP bridge, and executes the
tests in parallel against mock microservices.

---

## Architecture

```
prd.json
   │
   ▼
planner/test_planner.py          ← "agentic" planning layer
   │  generates
   ▼
tests/generated_test_plan.json   ← test matrix (driving pytest parametrize)
   │
   ▼
runner/orchestrator.py
   ├─ Phase 1: generate plan
   ├─ Phase 2: MCP health check ──► mcp-server:8001/system/status
   └─ Phase 3: pytest -n auto   ──► alert-api:8000/api/v1/alerts
                                         │
                                    ┌────┴────┐
                                 SQLite    mock Slack
                               (batch DB)  (mcp-server)
```

### Components

| Component | Description |
|-----------|-------------|
| `mock_servers/alert_api` | FastAPI service implementing the PRD routing rules |
| `mock_servers/mcp_server` | System status + mock Slack capture endpoint |
| `planner/test_planner.py` | Parses PRD JSON → emits `generated_test_plan.json` |
| `tests/conftest.py` | Loads plan, MCP health fixture, shared httpx clients |
| `tests/test_alerts.py` | Parametrized tests driven by the plan |
| `runner/orchestrator.py` | Three-phase orchestration pipeline |

---

## Quick Start

### Prerequisites

- Docker + Docker Compose
- Python 3.12+

### 1. Start the mock services

```bash
docker compose up -d
```

Wait ~15 seconds for both containers to pass their health checks.

### 2. Install test dependencies

```bash
pip install -r requirements.txt
```

### 3. Run the full orchestrated pipeline

```bash
python -m runner.orchestrator
```

This will:
1. Generate `tests/generated_test_plan.json` from `prd.json`
2. Poll MCP `/system/status` until ready
3. Run all tests in parallel via pytest-xdist

### 4. Run tests directly (after services are up)

```bash
# Parallel (default)
pytest tests/ -n auto -v

# Sequential (for debugging)
pytest tests/ -v
```

### 5. Generate the test plan only

```bash
python -m planner.test_planner
```

Output: `tests/generated_test_plan.json`

---

## Service Endpoints

### Alert API (`localhost:8000`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness probe |
| `POST` | `/api/v1/alerts` | Route an alert (see PRD rules) |
| `GET` | `/api/v1/alerts/batched` | Query the LOW-alert batch store |

**POST /api/v1/alerts payload:**

```json
{
  "severity": "CRITICAL",
  "source": "GCP",
  "message": "Unauthorized access detected",
  "alert_id": "optional-uuid-for-tracing"
}
```

### MCP Server (`localhost:8001`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness probe |
| `GET` | `/system/status` | Environment readiness (MCP bridge) |
| `POST` | `/mock/slack` | Slack webhook capture |
| `GET` | `/mock/slack/messages` | Inspect captured messages (`?alert_id=`) |
| `DELETE` | `/mock/slack/messages` | Reset Slack capture store |

---

## Test Matrix (generated from PRD)

| Test ID | Requirement | Category | Assertion |
|---------|-------------|----------|-----------|
| REQ-001-TC-001 | REQ-001 | happy_path | CRITICAL+GCP → Slack, within_sla=True |
| REQ-001-TC-002 | REQ-001 | negative | CRITICAL+AWS → NOT routed |
| REQ-001-TC-003 | REQ-001 | negative | HIGH+GCP → NOT routed |
| REQ-001-TC-004 | REQ-001 | meta | req_id=REQ-001 in response |
| REQ-002-TC-001 | REQ-002 | happy_path | LOW+GCP → batched, no Slack |
| REQ-002-TC-002 | REQ-002 | happy_path | LOW+AWS → batched, no Slack |
| REQ-002-TC-003 | REQ-002 | bulk | 5x LOW → all in DB, no Slack |
| REQ-002-TC-004 | REQ-002 | meta | req_id=REQ-002 in response |

---

## Parallel Execution Design

Tests use `pytest-xdist` (`-n auto`). Thread safety is ensured by:

- Each test case generates a **fresh UUID** `alert_id` at execution time.
- Slack message assertions **filter by `alert_id`** — workers never see each other's messages.
- DB assertions also filter by `alert_id` via `GET /api/v1/alerts/batched?alert_id=`.

This means all 8 test cases can run fully in parallel with no shared mutable state.

---

## Project Structure

```
agentic-test-engine/
├── docker-compose.yml
├── prd.json                          ← product requirements input
├── requirements.txt
├── pytest.ini
├── planning.md                       ← engineering design artifact
├── mock_servers/
│   ├── alert_api/
│   │   ├── Dockerfile
│   │   ├── main.py                   ← FastAPI alert routing service
│   │   └── requirements.txt
│   └── mcp_server/
│       ├── Dockerfile
│       ├── main.py                   ← MCP status + Slack capture
│       └── requirements.txt
├── planner/
│   └── test_planner.py              ← PRD parser + test matrix generator
├── tests/
│   ├── conftest.py                  ← fixtures, MCP gate, plan loader
│   ├── test_alerts.py               ← parametrized test suite
│   └── generated_test_plan.json    ← auto-generated (git-ignored)
├── runner/
│   └── orchestrator.py             ← three-phase pipeline
└── .github/
    └── workflows/
        └── ci.yml                  ← GitHub Actions CI
```

---

## Stopping Services

```bash
docker compose down -v
```

The `-v` flag removes the SQLite data volume for a clean restart.

---

## Design Document

See [planning.md](planning.md) for the full engineering design covering:

- AI & Agentic strategy (LLM tool-calling scale-up path)
- MCP architecture (infrastructure bridge structural overview)
- CI/CD & Observability (GitHub Actions + Grafana metrics pipeline)
