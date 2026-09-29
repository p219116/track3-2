# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0
"""Agent tools for the HR Agentic Solution, running locally.

  HR policy RAG        : search_hr_policy                     (Vertex AI Search or local store)
  WorkWeek HCM         : get_employee_balances, get_leave_requests,
                         request_time_off, cancel_leave_request,
                         get_personal_info, update_personal_info
  ServiceImmediately   : list_tickets, get_ticket_details, create_ticket,
                         add_ticket_comment, request_human_escalation

Security notes:
  * Tools do NOT accept an `employee_id` argument. The employee is always the
    current session user, so "show me EMP-202's leave" cannot read someone
    else's data.
  * Every tool honors the CISO kill switch (CISO_KILL_SWITCH_ACTIVE=true).
"""

import logging
from typing import Any, Optional

from app import config, vertex_search
from app.mcp_client import serviceimmediately_client, workweek_client
from app.rag_engine import policy_vector_store
from app.security import check_kill_switch

logger = logging.getLogger(__name__)


# ===========================================================================
# 1. HR Policy RAG
# ===========================================================================
def search_hr_policy(query: str, category: Optional[str] = "General") -> dict[str, Any]:
    """Searches the official HR policy handbook and returns grounded excerpts with citations.

    Args:
        query: The policy question or keywords (Korean or English), e.g. 'parental leave duration', '육아휴직'.
        category: Optional filter: 'Leave', 'RemoteWork', 'Expenses', 'CodeOfConduct', or 'General'.
    """
    check_kill_switch()
    if config.USE_VERTEX_SEARCH:
        try:
            result = vertex_search.search(query=query, top_k=3)
            if result["status"] == "SUCCESS":
                return result
        except Exception as e:  # noqa: BLE001 - any failure falls back to local search
            logger.warning("Vertex AI Search failed (%s); using local search.", e)
    result = policy_vector_store.search(query=query, category=category, top_k=3)
    result["source"] = "LOCAL"
    return result


# ===========================================================================
# 2. WorkWeek HCM
# ===========================================================================
def get_employee_balances() -> dict[str, Any]:
    """Returns the current user's remaining Vacation and Sick leave balances."""
    check_kill_switch()
    return workweek_client.call_tool(
        "get_employee_balances", {"employee_id": config.EMPLOYEE_ID}
    )


def get_leave_requests() -> dict[str, Any]:
    """Returns the current user's leave request history (request_id, dates, type, days)."""
    check_kill_switch()
    return workweek_client.call_tool(
        "get_leave_requests", {"employee_id": config.EMPLOYEE_ID}
    )


def request_time_off(
    leave_type: str, start_date: str, end_date: str, days: float
) -> dict[str, Any]:
    """Submits a leave request for the current user. Call ONLY after the user explicitly confirmed.

    Args:
        leave_type: 'Vacation' or 'Sick'.
        start_date: First day of leave, YYYY-MM-DD.
        end_date: Last day of leave, YYYY-MM-DD.
        days: Number of working days requested (0.5 for a half day).
    """
    check_kill_switch()
    if leave_type not in ("Vacation", "Sick"):
        return {"status": "ERROR", "error": "leave_type must be 'Vacation' or 'Sick'."}
    return workweek_client.call_tool(
        "request_time_off",
        {
            "employee_id": config.EMPLOYEE_ID,
            "leave_type": leave_type,
            "start_date": start_date,
            "end_date": end_date,
            "days": float(days),
        },
    )


def cancel_leave_request(request_id: int) -> dict[str, Any]:
    """Cancels one of the current user's leave requests and refunds the days. Call ONLY after explicit confirmation.

    Args:
        request_id: The request_id from get_leave_requests.
    """
    check_kill_switch()
    return workweek_client.call_tool(
        "cancel_leave_request",
        {"employee_id": config.EMPLOYEE_ID, "request_id": int(request_id)},
    )


def get_personal_info() -> dict[str, Any]:
    """Returns the current user's personal contact details (home address and phone number)."""
    check_kill_switch()
    return workweek_client.call_tool(
        "get_personal_info", {"employee_id": config.EMPLOYEE_ID}
    )


def update_personal_info(
    address: Optional[str] = None, phone: Optional[str] = None
) -> dict[str, Any]:
    """Updates the current user's home address and/or phone number. Call ONLY after explicit confirmation.

    Fields that are not provided keep their current value (read-before-write).

    Args:
        address: New home address.
        phone: New phone number.
    """
    check_kill_switch()
    if not address and not phone:
        return {"status": "ERROR", "error": "Provide at least one of address or phone."}

    if not address or not phone:
        current = get_personal_info()
        text = str(current.get("data", ""))
        for line in text.splitlines():
            key, _, value = line.partition(":")
            if not address and "address" in key.lower():
                address = value.strip()
            if not phone and "phone" in key.lower():
                phone = value.strip()
        if not address or not phone:
            return {
                "status": "ERROR",
                "error": "Could not read the current profile to preserve unchanged fields.",
            }

    return workweek_client.call_tool(
        "update_personal_info",
        {"employee_id": config.EMPLOYEE_ID, "address": address, "phone": phone},
    )


# ===========================================================================
# 3. ServiceImmediately ITSM
# ===========================================================================
def list_tickets() -> dict[str, Any]:
    """Lists all ServiceImmediately IT tickets requested by the current user."""
    check_kill_switch()
    return serviceimmediately_client.call_tool(
        "list_tickets", {"employee_id": config.EMPLOYEE_ID}
    )


def get_ticket_details(ticket_id: str) -> dict[str, Any]:
    """Returns status, assignee and comments of one of the current user's tickets.

    Args:
        ticket_id: The ticket ID, e.g. 'INC0000234'.
    """
    check_kill_switch()
    result = list_tickets()
    tickets = result.get("data")
    if isinstance(tickets, list):
        for t in tickets:
            if t.get("ticket_id") == ticket_id:
                return {"status": "SUCCESS", "data": t}
    return {"status": "NOT_FOUND", "error": f"No ticket {ticket_id} for this user."}


def create_ticket(
    category: str,
    short_description: str,
    priority: Optional[str] = "3 - Moderate",
    assignment_group: Optional[str] = "Service Desk",
) -> dict[str, Any]:
    """Creates a new ServiceImmediately IT incident / request ticket for the current user.

    Args:
        category: e.g. 'Inquiry / Help', 'Hardware', 'Software', 'Network'.
        short_description: Summary of the issue.
        priority: '1 - Critical', '2 - High', '3 - Moderate', or '4 - Low'.
        assignment_group: Assignment group, defaults to 'Service Desk'.
    """
    check_kill_switch()
    return serviceimmediately_client.call_tool(
        "create_ticket",
        {
            "requested_by": config.EMPLOYEE_ID,
            "category": category,
            "short_description": short_description,
            "priority": priority or "3 - Moderate",
            "assignment_group": assignment_group or "Service Desk",
        },
    )


def add_ticket_comment(ticket_id: str, comment: str) -> dict[str, Any]:
    """Appends a comment to one of the current user's tickets.

    Args:
        ticket_id: The ticket ID, e.g. 'INC0000234'.
        comment: The comment message.
    """
    check_kill_switch()
    return serviceimmediately_client.call_tool(
        "add_ticket_comment",
        {"ticket_id": ticket_id, "author": config.EMPLOYEE_ID, "comment": comment},
    )


def request_human_escalation(
    urgency_reason: str, ticket_id: Optional[str] = None
) -> dict[str, Any]:
    """Emergency break-glass: immediately escalates an issue to a live human agent.

    Args:
        urgency_reason: Why this is urgent / why a human is needed.
        ticket_id: Existing ticket to escalate. If omitted, a new Critical ticket is opened.
    """
    check_kill_switch()
    if not ticket_id:
        created = create_ticket(
            category="Inquiry / Help",
            short_description=f"[EMERGENCY ESCALATION] {urgency_reason}",
            priority="1 - Critical",
        )
        return {"status": created.get("status"), "escalation": "NEW_CRITICAL_TICKET", "data": created.get("data")}
    return add_ticket_comment(ticket_id, f"[EMERGENCY ESCALATION]: {urgency_reason}")
