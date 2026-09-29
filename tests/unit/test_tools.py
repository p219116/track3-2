# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0
"""Offline unit tests (no network, no LLM)."""

import pytest

from app import config, tools


class FakeMcp:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self.responses.get(name, {"status": "SUCCESS", "data": "ok"})


@pytest.fixture
def fakes(monkeypatch):
    ww, si = FakeMcp(), FakeMcp()
    monkeypatch.setattr(tools, "workweek_client", ww)
    monkeypatch.setattr(tools, "serviceimmediately_client", si)
    monkeypatch.setattr(config, "EMPLOYEE_ID", "EMP-TEST")
    return ww, si


def test_hcm_tools_always_use_session_employee(fakes):
    ww, _ = fakes
    tools.get_employee_balances()
    tools.get_leave_requests()
    tools.request_time_off("Vacation", "2026-10-02", "2026-10-02", 1)
    tools.cancel_leave_request(42)
    tools.get_personal_info()
    tools.update_personal_info(address="1 Main St", phone="+65 1111 2222")
    assert all(args["employee_id"] == "EMP-TEST" for _, args in ww.calls)
    assert len(ww.calls) == 6


def test_itsm_tools_always_use_session_employee(fakes):
    _, si = fakes
    tools.list_tickets()
    tools.create_ticket("Hardware", "Monitor broken")
    tools.add_ticket_comment("INC1", "any update?")
    assert si.calls[0][1]["employee_id"] == "EMP-TEST"
    assert si.calls[1][1]["requested_by"] == "EMP-TEST"
    assert si.calls[2][1]["author"] == "EMP-TEST"


def test_request_time_off_rejects_unknown_leave_type(fakes):
    ww, _ = fakes
    assert tools.request_time_off("Holiday", "2026-10-02", "2026-10-02", 1)["status"] == "ERROR"
    assert ww.calls == []


def test_update_personal_info_preserves_unchanged_field(fakes):
    ww, _ = fakes
    ww.responses["get_personal_info"] = {
        "status": "SUCCESS",
        "data": "Employee EMP-TEST\nAddress: 80 Pasir Panjang Rd\nPhone: +65 6789 0123",
    }
    tools.update_personal_info(phone="+65 9999 0000")
    name, args = ww.calls[-1]
    assert name == "update_personal_info"
    assert args["address"] == "80 Pasir Panjang Rd"
    assert args["phone"] == "+65 9999 0000"


def test_escalation_without_ticket_opens_critical_ticket(fakes):
    _, si = fakes
    tools.request_human_escalation("Client demo down in 10 minutes")
    name, args = si.calls[-1]
    assert name == "create_ticket"
    assert args["priority"] == "1 - Critical"


def test_escalation_with_ticket_adds_comment(fakes):
    _, si = fakes
    tools.request_human_escalation("urgent", ticket_id="INC7")
    name, args = si.calls[-1]
    assert name == "add_ticket_comment"
    assert args["ticket_id"] == "INC7"


def test_kill_switch_blocks_every_tool(fakes, monkeypatch):
    monkeypatch.setenv("CISO_KILL_SWITCH_ACTIVE", "true")
    with pytest.raises(RuntimeError, match="Kill Switch"):
        tools.get_employee_balances()
    with pytest.raises(RuntimeError, match="Kill Switch"):
        tools.search_hr_policy("leave")


def test_policy_search_returns_citations_in_korean_and_english(monkeypatch):
    monkeypatch.setattr(config, "USE_VERTEX_SEARCH", False)
    for q in ("출산휴가 기간", "maternity leave"):
        r = tools.search_hr_policy(q, "Leave")
        assert r["status"] == "SUCCESS"
        assert r["source"] == "LOCAL"
        assert "§" in r["primary_citation"]


def test_policy_search_uses_vertex_ai_search_when_enabled(monkeypatch):
    monkeypatch.setattr(config, "USE_VERTEX_SEARCH", True)
    hit = {"status": "SUCCESS", "results": [{}], "primary_citation": "X §1", "source": "VERTEX_AI_SEARCH"}
    monkeypatch.setattr(tools.vertex_search, "search", lambda query, top_k: hit)
    assert tools.search_hr_policy("maternity leave")["source"] == "VERTEX_AI_SEARCH"


def test_policy_search_falls_back_to_local_when_vertex_fails(monkeypatch):
    monkeypatch.setattr(config, "USE_VERTEX_SEARCH", True)

    def boom(query, top_k):
        raise RuntimeError("network down")

    monkeypatch.setattr(tools.vertex_search, "search", boom)
    r = tools.search_hr_policy("maternity leave", "Leave")
    assert r["status"] == "SUCCESS"
    assert r["source"] == "LOCAL"


def test_agent_topology():
    from app.agent import root_agent

    assert root_agent.name == "concierge_agent"
    subs = {a.name: {t.__name__ for t in a.tools} for a in root_agent.sub_agents}
    assert subs == {
        "hr_policy_rag_agent": {"search_hr_policy"},
        "hr_hcm_agent": {
            "get_employee_balances",
            "get_leave_requests",
            "request_time_off",
            "cancel_leave_request",
            "get_personal_info",
            "update_personal_info",
        },
        "itsm_support_agent": {
            "list_tickets",
            "get_ticket_details",
            "create_ticket",
            "add_ticket_comment",
            "request_human_escalation",
        },
    }
