"""Supabase access token 검증 — server `require-user.ts`와 동일 경로(remote getUser).

JWT를 로컬에서 까지 않고 Supabase Auth(`/auth/v1/user`)에 위임한다(폐기·만료 토큰까지 잡힘).
연결당 1회(session_init)만 호출 → 음성 지연에 영향 없음.
fail-safe: 검증 불가/오류면 None 반환(= 인증 실패로 처리, 연결 거절).
"""
from __future__ import annotations

import logging

import httpx

from .config import SUPABASE_ANON_KEY, SUPABASE_URL

_log = logging.getLogger("moly-voice")


async def verify_token(token: str) -> str | None:
    """Supabase access token → user.id. 무효/오류/미설정이면 None."""
    if not (SUPABASE_URL and SUPABASE_ANON_KEY):
        _log.warning("SUPABASE_URL/ANON_KEY 미설정 — 토큰 검증 불가(인증 거절)")
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={
                    "apikey": SUPABASE_ANON_KEY,
                    "Authorization": f"Bearer {token}",
                },
            )
    except Exception as e:  # noqa: BLE001  # 네트워크/DNS/타임아웃 등 — 거절(None)
        _log.warning("토큰 검증 오류: %r", e)
        return None
    if r.status_code != 200:  # 401=무효/만료, 그 외=Supabase 오류 — 모두 거절
        _log.info("토큰 검증 실패: HTTP %s", r.status_code)
        return None
    uid = (r.json() or {}).get("id")  # getUser 응답의 user.id
    return uid or None
