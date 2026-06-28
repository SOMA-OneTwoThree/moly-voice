"""moly-llm 장기기억 엔드포인트 호출 (세션시작 load / 세션종료 commit).

전부 fail-safe: 메모리는 부가기능이라 실패해도 대화는 계속돼야 한다.
- load: WS 연결 시 1회 → memory_text(없으면 ""). 세션 내내 고정해 매 턴 forward.
- commit: WS 종료 시 1회 → transcript를 mem0에 적재(비동기, 결과 무시).
"""
from __future__ import annotations

import logging
import time

import httpx

from .alerts import alert, internal_headers
from .config import (
    DEMO_USER_ID,
    MEMORY_COMMIT_URL,
    MEMORY_LOAD_TIMEOUT_S,
    MEMORY_LOAD_URL,
)

_log = logging.getLogger("moly-voice")


async def load_memory(user_id: str = DEMO_USER_ID) -> str:
    """세션시작 장기기억 로드. 실패 시 ""(메모리 없이 진행)."""
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=MEMORY_LOAD_TIMEOUT_S) as client:
            r = await client.post(MEMORY_LOAD_URL, json={"user_id": user_id},
                                  headers=internal_headers())
            r.raise_for_status()
            text = r.json().get("memory_text", "") or ""
            _log.info("memory load %dms (chars=%d)",
                      int((time.monotonic() - t0) * 1000), len(text))
            return text
    except Exception as e:  # noqa: BLE001
        _log.warning("memory load 실패 %dms(메모리 없이 진행): %r",
                     int((time.monotonic() - t0) * 1000), e)
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
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                MEMORY_COMMIT_URL, json={"user_id": user_id, "messages": convo},
                headers=internal_headers(),
            )
            # 응답 status를 반드시 확인 — moly-llm 커밋(mem0 add) 실패가 조용히 묻히던 버그.
            # 4xx/5xx면 본문 일부를 담아 알림(원인 추적용).
            if r.status_code >= 400:
                raise RuntimeError(f"commit {r.status_code}: {r.text[:300]}")
    except Exception as e:  # noqa: BLE001
        _log.warning("memory commit 실패: %r", e)
        await alert(repr(e), context="memory commit 실패")
