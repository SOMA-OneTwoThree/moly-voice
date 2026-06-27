"""ElevenLabs Scribe v2 Realtime STT — STTStream 구현.

검증된 프로토콜(docs/api-reference/speech-to-text realtime):
- WS: wss://api.elevenlabs.io/v1/speech-to-text/realtime?model_id=scribe_v2_realtime
      &audio_format=pcm_16000&language_code=<en>&commit_strategy=manual
- 인증: xi-api-key 헤더(TTS와 동일 키 재사용)
- 오디오: {"message_type":"input_audio_chunk","audio_base_64":<b64>,"commit":false,"sample_rate":16000}
- finalize(버튼 end): 같은 메시지에 audio_base_64="" + commit:true → 세그먼트 강제 확정
- 결과: partial_transcript(interim) / committed_transcript(final, manual commit 응답)

푸시투토크라 commit_strategy=manual: 우리가 commit 보낼 때만 committed_transcript가 온다.
그래서 results()는 committed 하나 받으면 턴 종료(Deepgram의 Metadata break와 동일 역할).
"""
from __future__ import annotations

import base64
import json
import logging
from typing import AsyncIterator

import websockets

from ..config import ELEVENLABS_API_KEY, STT_LANGUAGE

_log = logging.getLogger("moly-voice")

_SR = 16000
_MODEL = "scribe_v2_realtime"
_BASE = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"

# 종료/오류로 간주하는 message_type (results 루프 중단).
_ERROR_TYPES = {
    "error", "auth_error", "quota_exceeded", "rate_limited", "queue_overflow",
    "resource_exhausted", "session_time_limit_exceeded", "input_error",
    "chunk_size_exceeded", "transcriber_error", "unaccepted_terms",
}


class ElevenLabsScribeStream:
    """한 발화(턴) 동안 열리는 Scribe v2 Realtime 스트림."""

    name = "elevenlabs"  # 측정·로그 라벨

    def __init__(self) -> None:
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._sent_chunks = 0
        self._sent_bytes = 0

    async def open(self) -> None:
        qs = (
            f"model_id={_MODEL}&audio_format=pcm_{_SR}"
            f"&language_code={STT_LANGUAGE}&commit_strategy=manual"
        )
        url = f"{_BASE}?{qs}"
        hdrs = {"xi-api-key": ELEVENLABS_API_KEY}
        try:  # websockets 신버전(additional_headers) / 구버전(extra_headers) 호환
            self._ws = await websockets.connect(url, additional_headers=hdrs, max_size=None)
        except TypeError:
            self._ws = await websockets.connect(url, extra_headers=hdrs, max_size=None)
        # session_started(또는 auth/입력 에러) 1건 소비해 준비 확인
        raw = await self._ws.recv()
        mt = json.loads(raw).get("message_type")
        if mt in _ERROR_TYPES:
            raise RuntimeError(f"Scribe open 실패: {mt}")
        if mt != "session_started":
            _log.warning("Scribe open: 예상 밖 첫 메시지 type=%s", mt)

    async def send_audio(self, pcm: bytes) -> None:
        if not self._ws:
            return
        self._sent_chunks += 1
        self._sent_bytes += len(pcm)
        await self._ws.send(json.dumps({
            "message_type": "input_audio_chunk",
            "audio_base_64": base64.b64encode(pcm).decode("ascii"),
            "commit": False,
            "sample_rate": _SR,
        }))

    async def finalize(self) -> None:
        """버튼 end → 빈 오디오 + commit:true로 현재 세그먼트 강제 확정."""
        _log.info("Scribe sent: chunks=%d bytes=%d", self._sent_chunks, self._sent_bytes)
        if self._ws:
            await self._ws.send(json.dumps({
                "message_type": "input_audio_chunk",
                "audio_base_64": "",
                "commit": True,
                "sample_rate": _SR,
            }))

    async def results(self) -> AsyncIterator[tuple[str, bool]]:
        """(transcript, is_final). manual commit 응답(committed) 받으면 턴 종료."""
        if not self._ws:
            return
        async for raw in self._ws:
            msg = json.loads(raw)
            mt = msg.get("message_type")
            if mt == "partial_transcript":
                txt = msg.get("text", "")
                if txt:
                    yield txt, False
            elif mt in ("committed_transcript", "committed_transcript_with_timestamps"):
                txt = msg.get("text", "")
                if txt:
                    yield txt, True
                break  # manual commit 응답 = 이 턴 세그먼트 끝
            elif mt in _ERROR_TYPES:
                _log.warning("Scribe error: %s", raw[:200])
                break
            # session_started 등 기타는 무시

    async def close(self) -> None:
        if self._ws:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None
