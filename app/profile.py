"""사용자 프로필(닉네임) 조회 — Supabase profiles 직접(RLS).

닉네임은 확정적이고 항상 정확해야 하는 값이라 mem0(벡터, 추출 기반)에 넣지 않고,
연결 시점에 여기서 가져와 memory_text에 통제된 형태로 붙인다(기억 외부 주입).
RLS(profiles_select_own)가 본인 row만 반환하므로 user 토큰을 그대로 전달한다.
fail-safe: 실패/미온보딩/토큰 없음이면 None(이름 없이 진행).
"""
from __future__ import annotations

import logging

import httpx

from .config import SUPABASE_ANON_KEY, SUPABASE_URL

_log = logging.getLogger("moly-voice")


async def fetch_nickname(token: str, user_id: str) -> str | None:
    """Supabase profiles에서 본인 닉네임 조회. 없거나 실패면 None."""
    if not (SUPABASE_URL and SUPABASE_ANON_KEY and token):
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/profiles",
                params={"id": f"eq.{user_id}", "select": "nickname", "limit": "1"},
                headers={"apikey": SUPABASE_ANON_KEY,
                         "Authorization": f"Bearer {token}"},
            )
    except Exception as e:  # noqa: BLE001  # 네트워크/타임아웃 — 이름 없이 진행
        _log.warning("닉네임 조회 오류: %r", e)
        return None
    if r.status_code != 200:  # 401(무효 토큰) 등 — 조용히 스킵
        _log.info("닉네임 조회 실패: HTTP %s", r.status_code)
        return None
    rows = r.json() or []  # RLS: 본인 row만, 미온보딩이면 []
    nick = rows[0].get("nickname") if rows else None
    return nick.strip() if isinstance(nick, str) and nick.strip() else None
