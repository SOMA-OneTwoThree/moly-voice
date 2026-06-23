"""moly-llm /chat 호출 (async SSE).

계약: POST {messages:[{role,content}], user_id?} → data:{delta} × N → data:{done|error}
(하위호환으로 {text}도 가능하나 여기선 messages[]로 히스토리 전달.)
"""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .config import LLM_URL


async def stream_reply(messages: list[dict], user_id: str | None) -> AsyncIterator[str]:
    """LLM 응답 delta 문자열을 순서대로 yield."""
    body: dict = {"messages": messages}
    if user_id:
        body["user_id"] = user_id
    buf = ""
    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream("POST", LLM_URL, json=body) as r:
            if r.status_code != 200:
                await r.aread()
                raise RuntimeError(f"LLM {r.status_code}: {r.text[:200]}")
            async for chunk in r.aiter_text():
                buf += chunk
                while "\n\n" in buf:
                    frame, buf = buf.split("\n\n", 1)
                    for line in frame.splitlines():
                        if not line.startswith("data:"):
                            continue
                        payload = json.loads(line[5:].strip())
                        if payload.get("error"):
                            raise RuntimeError(f"LLM error: {payload['error']}")
                        delta = payload.get("delta")
                        if delta:
                            yield delta
