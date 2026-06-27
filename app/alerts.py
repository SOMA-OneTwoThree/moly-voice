"""에러 알림(Slack) + 내부 서비스 호출 헤더.

- alert(): Slack Incoming Webhook으로 운영 에러 통지. URL 없으면 no-op(로컬 안전).
  알림 실패가 본 기능을 죽이면 안 되므로 절대 예외를 전파하지 않는다.
- internal_headers(): moly-llm 내부 호출에 붙일 공유 시크릿 헤더(심층방어).
"""
from __future__ import annotations

import logging

import httpx

from .config import INTERNAL_SERVICE_TOKEN, SLACK_WEBHOOK_URL

_log = logging.getLogger("moly-voice")


async def alert(message: str, *, context: str = "") -> None:
    """운영 에러를 Slack으로. 항상 로그도 남긴다. 실패해도 조용히 통과(fail-safe)."""
    line = f"[moly-voice] {context}: {message}" if context else f"[moly-voice] {message}"
    _log.error(line)
    if not SLACK_WEBHOOK_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(SLACK_WEBHOOK_URL, json={"text": line})
    except Exception as e:  # noqa: BLE001  # 알림 실패는 무시(본 기능 보호)
        _log.warning("Slack alert 전송 실패: %r", e)


def internal_headers() -> dict[str, str]:
    """moly-llm 내부 호출용 헤더. 토큰 미설정이면 빈 dict(로컬)."""
    return {"X-Internal-Token": INTERNAL_SERVICE_TOKEN} if INTERNAL_SERVICE_TOKEN else {}
