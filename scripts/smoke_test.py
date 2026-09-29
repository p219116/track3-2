# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0
"""No-LLM smoke test: verifies config, MCP connectivity, and policy search (RAG).

Usage:  uv run python scripts/smoke_test.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app import tools  # noqa: E402
from app import vertex_search  # noqa: E402
from app.mcp_client import serviceimmediately_client, workweek_client  # noqa: E402

REQUIRED_WORKWEEK_TOOLS = {
    "get_employee_balances",
    "get_leave_requests",
    "request_time_off",
    "cancel_leave_request",
    "get_personal_info",
    "update_personal_info",
}
REQUIRED_ITSM_TOOLS = {"list_tickets", "create_ticket", "add_ticket_comment"}

results: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    try:
        ok, detail = fn()
    except Exception as e:  # noqa: BLE001
        ok, detail = False, f"{type(e).__name__}: {e}"
    results.append((name, ok, detail))


def _config():
    missing = config.missing_settings()
    return (not missing, "ok" if not missing else f"missing: {', '.join(missing)}")


def _mcp_tools(client, required):
    def run():
        names = {t["name"] for t in client.list_tools()}
        lacking = required - names
        return (not lacking, f"{len(names)} tools" if not lacking else f"missing: {lacking}")
    return run


def _balances():
    r = tools.get_employee_balances()
    return (r.get("status") == "SUCCESS", str(r.get("data") or r.get("error"))[:120])


def _requests():
    r = tools.get_leave_requests()
    data = r.get("data")
    if isinstance(data, list):
        n = len(data)
    elif isinstance(data, str):
        n = sum(1 for line in data.splitlines() if line.lstrip().startswith("- Request"))
    else:
        n = "?"
    return (r.get("status") == "SUCCESS", f"{n} requests")


def _rag():
    if config.USE_VERTEX_SEARCH:
        # Call Vertex AI Search directly so errors are shown instead of silently
        # falling back to local search.
        r = vertex_search.search("연차 휴가 일수")
    else:
        r = tools.search_hr_policy("연차 휴가 일수", "Leave")
    return (r.get("status") == "SUCCESS", str(r.get("primary_citation")))


def _profile():
    r = tools.get_personal_info()
    return (r.get("status") == "SUCCESS", "ok")


def _tickets():
    r = tools.list_tickets()
    data = r.get("data")
    n = len(data) if isinstance(data, list) else "?"
    return (r.get("status") == "SUCCESS", f"{n} tickets")


check("config (.env)", _config)
check("WorkWeek MCP tools/list", _mcp_tools(workweek_client, REQUIRED_WORKWEEK_TOOLS))
check("ITSM MCP tools/list", _mcp_tools(serviceimmediately_client, REQUIRED_ITSM_TOOLS))
check("get_employee_balances", _balances)
check("get_leave_requests", _requests)
check("get_personal_info", _profile)
check("list_tickets", _tickets)
rag_label = "Vertex AI Search" if config.USE_VERTEX_SEARCH else "local"
check(f"search_hr_policy ({rag_label})", _rag)

print(f"\nEmployee: {config.EMPLOYEE_ID}   MCP: {config.MCP_BASE_URL}\n")
for name, ok, detail in results:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name:<36} {detail.splitlines()[0] if detail else detail}")
failed = [r for r in results if not r[1]]
print(f"\n{'ALL PASSED' if not failed else f'{len(failed)} FAILED'}\n")
sys.exit(1 if failed else 0)
