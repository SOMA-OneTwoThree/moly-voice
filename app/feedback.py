"""'교정 받기' — 게이트웨이가 moly-llm /feedback 호출(브라우저는 직접 접근 불가).

사용자가 명시적으로 요청한 동작이라, 실패는 조용히 삼키지 않고 호출부가 에러를 통지한다.
"""
from __future__ import annotations

import httpx

from .alerts import internal_headers
from .config import DEMO_USER_ID, FEEDBACK_URL


async def request_feedback(messages: list[dict], user_id: str = DEMO_USER_ID) -> dict:
    """세션 대화 → 교정 결과 dict {has_corrections, corrections[]}. 빈 대화는 빈 결과."""
    convo = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m.get("content", "").strip()
    ]
    if not convo:
        return {"has_corrections": False, "corrections": []}

    async with httpx.AsyncClient(timeout=30.0) as client:  # 교정은 비스트리밍 1회(수 초)
        r = await client.post(FEEDBACK_URL, json={"user_id": user_id, "messages": convo},
                              headers=internal_headers())
        r.raise_for_status()
        return r.json()
