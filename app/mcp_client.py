# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0
"""Minimal JSON-RPC client for the WorkWeek HCM / ServiceImmediately MCP servers.

Speaks MCP Streamable HTTP: performs the `initialize` handshake once, keeps the
`Mcp-Session-Id`, and accepts both JSON and SSE (`text/event-stream`) responses.
The agent never talks to the backend any other way, which makes this the single
egress point to swap out later (e.g. to route through an API gateway).
"""

import itertools
import json
import logging
import threading
from typing import Any

import httpx

from app import config

logger = logging.getLogger(__name__)

_PROTOCOL_VERSION = "2025-06-18"


class McpClient:
    """Thin MCP-over-HTTP client (Streamable HTTP, JSON or SSE responses)."""

    def __init__(self, base_url: str, path: str, token: str, timeout: float):
        self.url = f"{base_url}{path}"
        self.token = token
        self.timeout = timeout
        self._ids = itertools.count(1)
        self._session_id: str | None = None
        self._initialized = False
        self._lock = threading.Lock()

    def _headers(self) -> dict[str, str]:
        headers = {
            "X-MCP-Token": self.token,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": _PROTOCOL_VERSION,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _send(self, payload: dict[str, Any]) -> httpx.Response:
        resp = httpx.post(
            self.url,
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return resp

    def _ensure_session(self) -> None:
        """Runs the MCP initialize handshake once per client."""
        with self._lock:
            if self._initialized:
                return
            self._session_id = None
            resp = self._send(
                {
                    "jsonrpc": "2.0",
                    "id": next(self._ids),
                    "method": "initialize",
                    "params": {
                        "protocolVersion": _PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "hr-agent-local", "version": "0.1.0"},
                    },
                }
            )
            self._session_id = resp.headers.get("mcp-session-id")
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            self._initialized = True

    def _post(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._ensure_session()
        payload = {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": method,
            "params": params,
        }
        try:
            resp = self._send(payload)
        except httpx.HTTPStatusError as e:
            # Session expired, server restarted, or a transient 5xx (e.g. Cloud
            # Run routed to an instance that doesn't know this session):
            # re-handshake once and retry.
            status = e.response.status_code
            if status not in (400, 404) and status < 500:
                raise
            self._initialized = False
            self._ensure_session()
            resp = self._send(payload)
        return _parse_response(resp)

    def list_tools(self) -> list[dict[str, Any]]:
        """Returns the tool definitions exposed by the MCP server."""
        return self._post("tools/list", {}).get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Calls an MCP tool and returns a JSON-serializable dict for the LLM."""
        try:
            data = self._post("tools/call", {"name": name, "arguments": arguments})
        except httpx.HTTPStatusError as e:
            logger.error("MCP %s failed: HTTP %s", name, e.response.status_code)
            return {"status": "ERROR", "error": f"HTTP {e.response.status_code}"}
        except httpx.HTTPError as e:
            logger.error("MCP %s failed: %s", name, e)
            return {"status": "ERROR", "error": f"Cannot reach vacation system: {e}"}

        if "error" in data:
            return {"status": "ERROR", "error": data["error"]}

        result = data.get("result", {})
        if result.get("isError"):
            return {"status": "ERROR", "error": _first_text(result)}

        text = _first_text(result)
        try:
            return {"status": "SUCCESS", "data": json.loads(text)}
        except (TypeError, ValueError):
            return {"status": "SUCCESS", "data": text}


def _parse_response(resp: httpx.Response) -> dict[str, Any]:
    """Returns the JSON-RPC message from a JSON or SSE (text/event-stream) body."""
    if "text/event-stream" not in resp.headers.get("content-type", ""):
        return resp.json()
    message: dict[str, Any] = {}
    for line in resp.text.splitlines():
        if line.startswith("data:"):
            data = json.loads(line[len("data:"):].strip())
            if "result" in data or "error" in data:
                message = data
    return message


def _first_text(result: dict[str, Any]) -> str | None:
    for item in result.get("content", []):
        if item.get("type") == "text":
            return item.get("text")
    return None


workweek_client = McpClient(
    base_url=config.MCP_BASE_URL,
    path=config.WORKWEEK_MCP_PATH,
    token=config.MCP_TOKEN,
    timeout=config.MCP_TIMEOUT_SECONDS,
)

serviceimmediately_client = McpClient(
    base_url=config.MCP_BASE_URL,
    path=config.ITSM_MCP_PATH,
    token=config.MCP_TOKEN,
    timeout=config.MCP_TIMEOUT_SECONDS,
)


def _resolve_employee_id() -> None:
    """If EMPLOYEE_ID is empty, asks WorkWeek which employee owns MCP_TOKEN."""
    if config.EMPLOYEE_ID or not (config.MCP_BASE_URL and config.MCP_TOKEN):
        return
    result = workweek_client.call_tool("get_current_employee_id", {})
    employee_id = str(result.get("data") or "").strip()
    if result.get("status") == "SUCCESS" and employee_id:
        config.EMPLOYEE_ID = employee_id
        logger.info("EMPLOYEE_ID auto-detected from MCP_TOKEN: %s", employee_id)
    else:
        logger.warning(
            "Could not auto-detect EMPLOYEE_ID (%s). Set EMPLOYEE_ID in .env.",
            result.get("error"),
        )


_resolve_employee_id()
