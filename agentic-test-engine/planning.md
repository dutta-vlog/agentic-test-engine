# Planning Document — Next-Gen Agentic Test Automation Engine

> Engineering thought process, architecture decisions, and scale-up strategy.

---

## 1. Problem Decomposition

The task is a three-layer problem:

| Layer | Concern | This implementation |
|-------|---------|---------------------|
| **Planning** | Convert requirements → test cases | `planner/test_planner.py` |
| **Infrastructure** | Healthy mock services to test against | Docker Compose (alert-api + mcp-server) |
| **Execution** | Run tests efficiently, gate on environment | pytest-xdist + MCP health fixture |

The central insight is that these layers must be **decoupled**: the planner emits a `generated_test_plan.json` consumed by the test runner. Changing the planner (e.g., from rule-based to LLM-based) requires no changes to the tests.

---

## 2. AI & Agentic Strategy

### 2.1 Current state: deterministic rule parsers

The current `test_planner.py` uses a registry of hand-coded parsers (`_parse_req_001`, `_parse_req_002`). Each parser knows the domain rules and generates happy-path, negative, bulk, and meta test cases. This is fast and correct for a known, stable requirement set.

### 2.2 Scale path: LLM-backed agentic planner

To scale to an enterprise PRD with hundreds of requirements, the parser registry is replaced with an LLM agent:

```
PRD JSON
   │
   ▼
┌──────────────────────────────────────────────────────┐
│  Planner Agent (Claude claude-sonnet-4-6 / Opus 4.8)         │
│                                                      │
│  System prompt:                                      │
│    "You are a senior QA architect. Given a software  │
│     requirement, generate exhaustive test cases in   │
│     the TestCase JSON schema. Cover: happy path,     │
│     negative, boundary, bulk, and metadata checks."  │
│                                                      │
│  Tools:                                              │
│    get_requirement(req_id)  → requirement dict       │
│    validate_test_case(tc)   → schema validation      │
│    store_test_case(tc)      → append to plan         │
│    get_existing_cases()     → dedup / gap analysis   │
└──────────────────────────────────────────────────────┘
   │
   ▼
generated_test_plan.json   (same schema, same consumer)
```

**Tool-calling pattern** (Claude API):

```python
tools = [
    {
        "name": "store_test_case",
        "description": "Persist a validated test case to the plan",
        "input_schema": TEST_CASE_SCHEMA,  # JSON Schema
    }
]

response = anthropic.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=4096,
    tools=tools,
    messages=[
        {"role": "user", "content": f"Generate test cases for: {json.dumps(requirement)}"}
    ],
)
```

The `store_test_case` tool call is schema-validated before persistence, so malformed LLM output is caught early. The same `generated_test_plan.json` drives the test runner — the execution layer is unaware whether a human or an LLM authored the plan.

### 2.3 Multi-agent pattern for enterprise scale

```
                    ┌─────────────┐
                    │  Orchestrator│  (Claude Code CLI / cron)
                    └──────┬──────┘
                           │ spawns
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
  │ Planner Agent│ │Validator Agent│ │Analyst Agent │
  │              │ │              │ │              │
  │ PRD → cases  │ │ dedup, gap   │ │ failure RCA  │
  │              │ │ analysis     │ │ on flaky runs│
  └──────────────┘ └──────────────┘ └──────────────┘
```

- **Planner**: generates test matrix from updated PRD diff (triggered on PR merge).
- **Validator**: reviews the plan for coverage gaps, calls `get_existing_cases()` to detect regressions before execution.
- **Analyst**: post-run, fetches logs via MCP and generates a natural-language root cause analysis for any failure.

### 2.4 Prompt engineering practices

| Technique | Application |
|-----------|-------------|
| **Few-shot examples** | Planner prompt includes 2 gold test cases per category |
| **Chain-of-thought** | "Think step by step: what can go wrong? What boundary values matter?" |
| **Schema-constrained output** | All LLM output goes through `store_test_case` tool — no free-form JSON parsing |
| **Constitutional review** | Validator agent checks: "Does each happy-path have a corresponding negative?" |

---

## 3. MCP (Model Context Protocol) Architecture

### 3.1 What MCP solves here

The test framework needs live context it cannot derive from static files:

- Is the target service healthy right now?
- What were the last 10 errors in the alert-api logs?
- Is the DB schema in the state the tests expect?

An MCP server bridges the LLM (or orchestrator) to these live data sources.

### 3.2 Structural overview

```
┌──────────────────────────────────────────────────────────────┐
│                    LLM / Claude Agent                        │
│                                                              │
│  "Before running, check if the system is ready.             │
│   If alert-api is down, fetch its last error log."           │
└────────────────────────┬─────────────────────────────────────┘
                         │ MCP tool calls (JSON-RPC over stdio/SSE)
┌────────────────────────▼─────────────────────────────────────┐
│                     MCP Server                               │
│                                                              │
│  Tools:                                                      │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ check_system_health()                                   │ │
│  │   → GET /system/status on each microservice             │ │
│  │   → returns {ready: bool, services: {name: status}}     │ │
│  ├─────────────────────────────────────────────────────────┤ │
│  │ get_recent_logs(service, tail_n)                        │ │
│  │   → queries CloudWatch / ELK Elasticsearch              │ │
│  │   → returns last N log lines for the named service      │ │
│  ├─────────────────────────────────────────────────────────┤ │
│  │ get_db_snapshot(table)                                  │ │
│  │   → runs a SELECT on the test DB via read-only replica  │ │
│  │   → returns row count and sample rows for assertions    │ │
│  ├─────────────────────────────────────────────────────────┤ │
│  │ get_env_config()                                        │ │
│  │   → returns service URLs, feature flags, env name       │ │
│  │   → used to parametrize tests against the right env     │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
│  Resources (read-only context injected into LLM context):   │
│    resource://alerts/schema    — OpenAPI spec for alert-api  │
│    resource://prd/current      — live PRD from Confluence    │
│    resource://runbook/alerting — on-call runbook             │
└──────────────────────────────────────────────────────────────┘
         │                │                │
         ▼                ▼                ▼
  alert-api          SQLite/RDS        CloudWatch / ELK
```

### 3.3 This project's MCP mock

The `mock_servers/mcp_server/main.py` implements a subset:

| Endpoint | MCP tool equivalent |
|----------|---------------------|
| `GET /system/status` | `check_system_health()` |
| `POST /mock/slack` | infrastructure side-effect capture |
| `GET /mock/slack/messages` | `get_slack_capture(alert_id)` |

In production the MCP server would be a long-running process (stdio or SSE transport) implementing the full MCP spec with proper capability negotiation.

### 3.4 How the agent uses MCP at runtime

```
Agent turn:
  1. invoke check_system_health()
     → {ready: true, services: {alert_api: "up", db: "up"}}
  2. dispatch test batch (pytest -n auto)
  3. on failure: invoke get_recent_logs("alert-api", tail_n=50)
     → "2024-06-07T12:34:56 ERROR sqlite3.OperationalError: disk full"
  4. agent generates RCA: "Test REQ-002-TC-003 failed because /data volume
     is full. Suggested fix: docker volume prune or increase volume size."
```

---

## 4. CI/CD & Observability

### 4.1 Pipeline architecture (GitHub Actions)

```
┌─────────────────────────────────────────────────────────────┐
│  Trigger: PR opened / push to main                          │
└──────────────────────────────┬──────────────────────────────┘
                               │
              ┌────────────────▼───────────────┐
              │  Stage 1: Lint & Type Check     │
              │    ruff check . && mypy .       │
              └────────────────┬───────────────┘
                               │
              ┌────────────────▼───────────────┐
              │  Stage 2: Build Docker Images   │
              │    docker compose build          │
              └────────────────┬───────────────┘
                               │
              ┌────────────────▼───────────────┐
              │  Stage 3: Start Services        │
              │    docker compose up -d         │
              │    + health-check polling       │
              └────────────────┬───────────────┘
                               │
              ┌────────────────▼───────────────┐
              │  Stage 4: Generate Test Plan    │
              │    python -m planner.test_planner│
              └────────────────┬───────────────┘
                               │
              ┌────────────────▼───────────────┐
              │  Stage 5: Run Tests             │
              │    pytest -n auto               │
              │    --junitxml=results/junit.xml │
              └────────────────┬───────────────┘
                               │
         ┌─────────────────────┼──────────────────────┐
         ▼                     ▼                      ▼
  Upload JUnit XML      Post PR comment          Push metrics
  (Actions artifact)    (test summary)        → Prometheus pushgateway
```

### 4.2 Observability with Grafana

**Metrics pipeline:**

```
pytest-json-report        Prometheus          Grafana
(per-test timings)  ──►  pushgateway   ──►  Dashboard
                          /metrics
```

**Key metrics collected:**

| Metric | Type | Label dimensions |
|--------|------|-----------------|
| `test_run_duration_seconds` | Histogram | `req_id`, `category` |
| `test_case_result` | Gauge (0/1) | `test_id`, `req_id`, `status` |
| `alert_routing_latency_seconds` | Histogram | `severity`, `source` |
| `test_plan_generated_cases_total` | Counter | `req_id` |
| `mcp_health_check_duration_seconds` | Histogram | `environment` |

**Grafana dashboard panels (JSON provisioned):**

1. **Test Health Overview** — pass/fail rate by req_id, last 7 days
2. **SLA Compliance** — histogram of `alert_routing_latency_seconds` vs 2 s threshold (REQ-001)
3. **Flaky Test Tracker** — tests with >1 failure in last 10 runs
4. **Plan Coverage** — stacked bar: happy_path / negative / bulk / meta per requirement

**Alerting rules:**

```yaml
# alerting_rules.yml
- alert: TestSuiteFailureRate
  expr: rate(test_case_result{status="failed"}[10m]) > 0.1
  for: 5m
  annotations:
    summary: "Test failure rate exceeded 10% — notify #qa-alerts"

- alert: AlertRoutingSLAViolation
  expr: histogram_quantile(0.95, alert_routing_latency_seconds) > 1.8
  for: 2m
  annotations:
    summary: "p95 alert routing approaching 2 s SLA"
```

### 4.3 Incremental PRD-driven test generation in CI

When a PR modifies `prd.json`, a webhook triggers the planner agent:

```
prd.json changed (PR) 
  → planner agent diffs old vs new requirements
  → generates only net-new test cases
  → commits generated_test_plan.json to the PR branch
  → CI picks up the updated plan in the next run
```

This closes the loop: product requirement → auto-generated test → CI validation → Grafana metric.

---

## 5. Architectural Decisions & Trade-offs

| Decision | Choice | Trade-off accepted |
|----------|--------|--------------------|
| SQLite for batch store | Simple, zero-ops | Not suitable for concurrent writes at scale; replace with Postgres in prod |
| In-memory Slack capture | Simple test isolation via alert_id filter | Lost on MCP server restart; acceptable for ephemeral test runs |
| pytest-xdist for parallelism | True parallel workers, no shared memory | Workers can't share session state; alert_id per-test pattern solves this |
| Deterministic planner (no LLM calls) | Reproducible, fast, no API key needed | Does not discover edge cases a human or LLM would; replace for prod |
| MCP as a FastAPI server | Familiar HTTP, easy to mock | Real MCP uses stdio/SSE transport; the interface contract is the same |

---

## 6. Next Steps for Production Readiness

1. **Replace rule parsers with Claude API tool-calling** — one `store_test_case` tool call per test case, schema-validated.
2. **Add `pytest-json-report`** and a post-test Prometheus pushgateway exporter.
3. **Provision Grafana dashboards as code** (Grafonnet / dashboard-as-JSON in the repo).
4. **Implement MCP stdio transport** so Claude Code CLI can call `check_system_health` natively.
5. **Add mutation testing** (mutmut) to verify test assertions catch real regressions.
6. **Postgres + Alembic** to replace SQLite for the batch alert store.
