# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0
"""HR Agentic Solution - local-only ADK multi-agent sample.

Multi-agent architecture:
  - concierge_agent      (Hub / Supervisor)  -> routes & answers composite queries
  - hr_policy_rag_agent  (Specialist)        -> grounded HR policy Q&A with citations
  - hr_hcm_agent         (Specialist)        -> WorkWeek leave balances / requests / profile
  - itsm_support_agent   (Specialist)        -> ServiceImmediately tickets & break-glass escalation

Run locally:  uv run adk web   (or)   uv run adk run app
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from google.adk.agents import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from app import config
from app.tools import (
    add_ticket_comment,
    cancel_leave_request,
    create_ticket,
    get_employee_balances,
    get_leave_requests,
    get_personal_info,
    get_ticket_details,
    list_tickets,
    request_human_escalation,
    request_time_off,
    search_hr_policy,
    update_personal_info,
)

retry_options = types.HttpRetryOptions(attempts=3)
gen_config = types.GenerateContentConfig(temperature=0.1)


def _session_context() -> str:
    now = datetime.now(ZoneInfo(config.TIMEZONE))
    return (
        f"Today is {now:%Y-%m-%d} ({now:%A}), timezone {config.TIMEZONE}. "
        f"The current user is employee {config.EMPLOYEE_ID}. "
        "You can only access this user's own records; politely refuse requests "
        "about other employees. Reply in the user's language (Korean if asked in Korean)."
    )


CONFIRMATION_RULE = """Write operations change real records. Before calling `request_time_off`,
`cancel_leave_request` or `update_personal_info`, show a short summary and ask the
user to confirm. Call the tool ONLY after the user explicitly says yes in a later message.
Resolve relative dates ("내일", "다음 주 금요일") using today's date."""


# =====================================================================
# 1. Specialist: HR Policy RAG
# =====================================================================
POLICY_INSTRUCTION = """You are the HR Policy RAG Specialist Agent.
Your sole purpose is to retrieve official HR policy rules using `search_hr_policy`.

1. Always call `search_hr_policy` with the user's query.
2. Ground all answers strictly in the retrieved policy text. Never invent terms.
3. Always cite the document and section, e.g. [Citation: ALTOSTRAT_SG_Handbook.pdf §2.1].
4. If an activity or claim is disallowed (e.g. pet bereavement, personal entertainment),
   explicitly say it is non-reimbursable or disallowed according to the Handbook.
5. If nothing relevant is returned, say the handbook does not cover it."""

hr_policy_rag_agent = Agent(
    name="hr_policy_rag_agent",
    model=Gemini(model=config.WORKER_MODEL, retry_options=retry_options),
    description="Specialist for grounded HR policies, benefits, guidelines, and citations.",
    instruction=lambda _ctx: f"{_session_context()}\n\n{POLICY_INSTRUCTION}",
    tools=[search_hr_policy],
    generate_content_config=gen_config,
)


# =====================================================================
# 2. Specialist: HR HCM (WorkWeek)
# =====================================================================
HCM_INSTRUCTION = f"""You are the HR HCM Specialist Agent for WorkWeek.

1. Leave balances: `get_employee_balances` (Vacation / Sick).
2. Leave history: `get_leave_requests` (includes request_id).
3. Book leave: `request_time_off` (dates in YYYY-MM-DD, days = working days, 0.5 = half day).
4. Cancel leave: `cancel_leave_request` with a request_id from `get_leave_requests`.
5. Profile: `get_personal_info`; update address/phone with `update_personal_info`
   (unchanged fields are preserved automatically).
6. After any change, confirm the result back to the user (and the new balance for leave).

{CONFIRMATION_RULE}"""

hr_hcm_agent = Agent(
    name="hr_hcm_agent",
    model=Gemini(model=config.WORKER_MODEL, retry_options=retry_options),
    description="Specialist for WorkWeek HCM: leave balances, leave booking/cancellation, profile and contact updates.",
    instruction=lambda _ctx: f"{_session_context()}\n\n{HCM_INSTRUCTION}",
    tools=[
        get_employee_balances,
        get_leave_requests,
        request_time_off,
        cancel_leave_request,
        get_personal_info,
        update_personal_info,
    ],
    generate_content_config=gen_config,
)


# =====================================================================
# 3. Specialist: ITSM (ServiceImmediately)
# =====================================================================
ITSM_INSTRUCTION = """You are the ITSM Specialist Agent for ServiceImmediately.

1. Create tickets: `create_ticket` with category, short_description, and priority.
2. List tickets: `list_tickets` to find the user's existing tickets (check for duplicates first).
3. Ticket details: `get_ticket_details` for status, assignee, and comments.
4. Add comments: `add_ticket_comment`.
5. Emergency break-glass: if the user expresses high urgency, a client emergency, or
   asks for a human, immediately call `request_human_escalation` (no confirmation needed)."""

itsm_support_agent = Agent(
    name="itsm_support_agent",
    model=Gemini(model=config.WORKER_MODEL, retry_options=retry_options),
    description="Specialist for ServiceImmediately ITSM: creating/viewing tickets, comments, and emergency human escalation.",
    instruction=lambda _ctx: f"{_session_context()}\n\n{ITSM_INSTRUCTION}",
    tools=[
        list_tickets,
        get_ticket_details,
        create_ticket,
        add_ticket_comment,
        request_human_escalation,
    ],
    generate_content_config=gen_config,
)


# =====================================================================
# 4. Supervisor Hub: Concierge
# =====================================================================
CONCIERGE_INSTRUCTION = f"""You are the Enterprise Concierge Chatbot Hub (HR Agentic Solution).
You give employees fast, grounded, unified help across HR policies, WorkWeek HCM, and IT Support.
Be polite, direct, and helpful.

1. Policy queries (leave rules, parental leave, expense limits, allowances):
   call `search_hr_policy` or transfer to `hr_policy_rag_agent`. Always include citations.
2. HCM (leave balance/history, book or cancel leave, view/update address or phone):
   use `get_employee_balances`, `get_leave_requests`, `get_personal_info`,
   or transfer to `hr_hcm_agent` for any booking, cancellation, or update.
3. IT support (view tickets, report incidents, break-glass human escalation):
   use `list_tickets`, `get_ticket_details`, `request_human_escalation`,
   or transfer to `itsm_support_agent` to create tickets or add comments.
4. Composite / multi-intent queries (e.g. "paternity policy AND my leave balance"):
   you MUST answer ALL parts in the same turn, e.g. call `search_hr_policy` AND
   `get_employee_balances`, then give one unified answer.

{CONFIRMATION_RULE}"""


def build_concierge_instruction(_ctx: ReadonlyContext) -> str:
    return f"{_session_context()}\n\n{CONCIERGE_INSTRUCTION}"


concierge_agent = Agent(
    name="concierge_agent",
    model=Gemini(model=config.SUPERVISOR_MODEL, retry_options=retry_options),
    description="Primary enterprise assistant and conversational concierge hub.",
    instruction=build_concierge_instruction,
    sub_agents=[hr_policy_rag_agent, hr_hcm_agent, itsm_support_agent],
    tools=[
        search_hr_policy,
        get_employee_balances,
        get_leave_requests,
        get_personal_info,
        list_tickets,
        get_ticket_details,
        request_human_escalation,
    ],
    generate_content_config=gen_config,
)

root_agent = concierge_agent

app = App(root_agent=root_agent, name="app")
