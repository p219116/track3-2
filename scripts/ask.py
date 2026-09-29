# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0
"""Sends one or more messages to the agent in a single local session and prints
tool calls + answers. Handy for scripted checks without the web UI.

Usage:
  uv run python scripts/ask.py "내 연차 며칠 남았어?"
  uv run python scripts/ask.py "다음 주 금요일 연차 신청해줘" "응, 신청해줘"
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from app.agent import app  # noqa: E402


async def main(messages: list[str]) -> None:
    runner = InMemoryRunner(app=app)
    session = await runner.session_service.create_session(
        app_name=app.name, user_id="local-user"
    )
    for msg in messages:
        print(f"\n👤 {msg}")
        content = types.Content(role="user", parts=[types.Part(text=msg)])
        async for event in runner.run_async(
            user_id="local-user", session_id=session.id, new_message=content
        ):
            for part in (event.content.parts if event.content else []) or []:
                if part.function_call:
                    print(f"   🔧 [{event.author}] {part.function_call.name}({dict(part.function_call.args or {})})")
                elif part.text and event.is_final_response():
                    print(f"🤖 [{event.author}] {part.text.strip()}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    asyncio.run(main(sys.argv[1:]))
