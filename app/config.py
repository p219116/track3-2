# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0
"""Local configuration loader.

All settings come from the project-root `.env` file (see `.env.example`).
Nothing is hardcoded: no project IDs, no tokens.
"""

import os
from pathlib import Path

import truststore
from dotenv import load_dotenv

# Use the OS certificate store for all TLS (python.org builds ship without a
# CA bundle, and corporate networks may use TLS inspection).
truststore.inject_into_ssl()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# --- Models (supervisor hub vs. specialists) -------------------------------
SUPERVISOR_MODEL = os.getenv("SUPERVISOR_MODEL", "gemini-2.5-flash")
WORKER_MODEL = os.getenv("WORKER_MODEL", "gemini-2.5-flash")

# --- Mock SaaS MCP backends (WorkWeek HCM + ServiceImmediately ITSM) -------
MCP_BASE_URL = os.getenv("MCP_BASE_URL", "").rstrip("/")
MCP_TOKEN = os.getenv("MCP_TOKEN", "")
WORKWEEK_MCP_PATH = os.getenv("WORKWEEK_MCP_PATH", "/work-week/mcp")
ITSM_MCP_PATH = os.getenv("ITSM_MCP_PATH", "/service-immediately/mcp")
MCP_TIMEOUT_SECONDS = float(os.getenv("MCP_TIMEOUT_SECONDS", "10"))

# --- Local user identity ---------------------------------------------------
# Local mode has no SSO. Every request acts as this single employee.
# Leave empty to auto-detect the employee that owns MCP_TOKEN (resolved in
# app/mcp_client.py).
EMPLOYEE_ID = os.getenv("EMPLOYEE_ID", "").strip()

# --- Local timezone for resolving relative dates ("next Friday") -----------
TIMEZONE = os.getenv("TIMEZONE", "Asia/Seoul")

# --- HR policy RAG: Vertex AI Search data store (vector store) -------------
# false -> in-memory local search over docs/policies (no GCP needed)
# true  -> Vertex AI Search data store created by scripts/setup_vector_store.py
#          (falls back to local search if the call fails)
USE_VERTEX_SEARCH = os.getenv("USE_VERTEX_SEARCH", "false").lower() in ("true", "1")
DATA_STORE_ID = os.getenv("DATA_STORE_ID", "hr-policies-local").strip()
DATA_STORE_LOCATION = os.getenv("DATA_STORE_LOCATION", "global").strip()
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()


def missing_settings() -> list[str]:
    """Returns the names of required settings that are not configured."""
    missing = []
    if not MCP_BASE_URL:
        missing.append("MCP_BASE_URL")
    if not MCP_TOKEN:
        missing.append("MCP_TOKEN")
    if MCP_TOKEN and not EMPLOYEE_ID:
        missing.append("EMPLOYEE_ID (auto-detect from MCP_TOKEN failed)")
    use_vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("true", "1")
    if use_vertex and not os.getenv("GOOGLE_CLOUD_PROJECT"):
        missing.append("GOOGLE_CLOUD_PROJECT")
    if not use_vertex and not os.getenv("GOOGLE_API_KEY"):
        missing.append("GOOGLE_API_KEY (or set GOOGLE_GENAI_USE_VERTEXAI=true)")
    if USE_VERTEX_SEARCH and not GOOGLE_CLOUD_PROJECT:
        missing.append("GOOGLE_CLOUD_PROJECT (required when USE_VERTEX_SEARCH=true)")
    if USE_VERTEX_SEARCH and not DATA_STORE_ID:
        missing.append("DATA_STORE_ID")
    return missing
