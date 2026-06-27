"""moly-llm 장기기억 엔드포인트 호출 (세션시작 load / 세션종료 commit).

전부 fail-safe: 메모리는 부가기능이라 실패해도 대화는 계속돼야 한다.
- load: WS 연결 시 1회 → memory_text(없으면 ""). 세션 내내 고정해 매 턴 forward.
- commit: WS 종료 시 1회 → transcript를 mem0에 적재(비동기, 결과 무시).
"""
from __future__ import annotations

import logging

import httpx

from .alerts import alert, internal_headers
from .config import DEMO_USER_ID, MEMORY_COMMIT_URL, MEMORY_LOAD_URL

_log = logging.getLogger("moly-voice")


async def load_memory(user_id: str = DEMO_USER_ID) -> str:
    """세션시작 장기기억 로드. 실패 시 ""(메모리 없이 진행)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(MEMORY_LOAD_URL, json={"user_id": user_id},
                                  headers=internal_headers())
            r.raise_for_status()
            return r.json().get("memory_text", "") or ""
    except Exception as e:  # noqa: BLE001
        _log.warning("memory load 실패(메모리 없이 진행): %r", e)
        await alert(repr(e), context="memory load 실패")
        return ""


async def commit_memory(messages: list[dict], user_id: str = DEMO_USER_ID) -> None:
    """세션종료 시 transcript 커밋. 빈 세션은 스킵. 실패 무시(fail-safe)."""
    convo = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m.get("content", "").strip()
    ]
    if not convo:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                MEMORY_COMMIT_URL, json={"user_id": user_id, "messages": convo},
                headers=internal_headers(),
            )
    except Exception as e:  # noqa: BLE001
        _log.warning("memory commit 실패: %r", e)
        await alert(repr(e), context="memory commit 실패")
