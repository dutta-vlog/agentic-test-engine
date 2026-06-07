"""Main test orchestration pipeline.

Three-phase execution model:
  Phase 1 — Plan   : Parse PRD, generate structured test matrix JSON
  Phase 2 — Verify : Gate execution behind an MCP system status check
  Phase 3 — Execute: Dispatch pytest with parallel workers (pytest-xdist)

Usage:
  python -m runner.orchestrator [--no-parallel]

Copyright 2024 Agentic Test Engine Project.
All Rights Reserved. Confidential.
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).parent.parent
MCP_URL = "http://localhost:8001"


def _generate_plan() -> dict:
    sys.path.insert(0, str(ROOT))
    from planner.test_planner import generate_test_plan

    plan = generate_test_plan()
    print(f"    feature  : {plan['feature']}")
    print(f"    total    : {plan['total_tests']} test cases")
    for req_id, count in plan["summary"]["by_req"].items():
        print(f"    {req_id}  : {count} cases")
    for cat, count in plan["summary"]["by_category"].items():
        print(f"    [{cat}] {count}")
    return plan


def _check_system_health(max_retries: int = 12, delay_s: int = 3) -> bool:
    """Poll the MCP /system/status endpoint until ready or exhausted."""
    for attempt in range(1, max_retries + 1):
        try:
            resp = httpx.get(f"{MCP_URL}/system/status", timeout=5)
            data = resp.json()
            if data.get("ready_for_testing"):
                print(f"    status   : {data['status']}")
                print(f"    services : {data['services']}")
                return True
        except Exception as exc:
            print(f"    attempt {attempt}/{max_retries}: {exc}")
        if attempt < max_retries:
            time.sleep(delay_s)
    return False


def _run_tests(parallel: bool = True) -> int:
    cmd = [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short", "--color=yes"]
    if parallel:
        cmd += ["-n", "auto"]
    result = subprocess.run(cmd, cwd=ROOT)
    return result.returncode


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Agentic Test Automation Engine Orchestrator")
    parser.add_argument("--no-parallel", action="store_true", help="Disable parallel test execution")
    args = parser.parse_args(argv)

    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║    Agentic Test Automation Engine — Orchestrator v1.0   ║")
    print("╚══════════════════════════════════════════════════════════╝\n")

    print("[Phase 1/3] Generating test plan from PRD …")
    _generate_plan()

    print("\n[Phase 2/3] Verifying system health via MCP infrastructure bridge …")
    if not _check_system_health():
        print("\n  ERROR: System not ready after retries. Aborting.")
        print("  Hint: docker-compose up -d && sleep 10")
        sys.exit(1)
    print("  ✓ System is healthy — proceeding to test execution\n")

    print("[Phase 3/3] Executing tests …")
    parallel = not args.no_parallel
    if parallel:
        print("  Mode: parallel (pytest-xdist auto workers)\n")
    else:
        print("  Mode: sequential\n")

    exit_code = _run_tests(parallel=parallel)

    status = "PASSED ✓" if exit_code == 0 else "FAILED ✗"
    print(f"\n══ Run complete — {status} (exit code: {exit_code}) ══\n")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
