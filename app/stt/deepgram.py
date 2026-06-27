"""Deepgram STT — Nova-3 실시간 스트리밍 (async).

harness(파일 기반, sync)를 live 프레임용 async로 개조.
push-to-talk라 endpointing(자동 발화종료)은 끄고, 버튼 end → finalize(CloseStream)로 최종화.
"""
from __future__ import annotations

import array
import json
import logging
from typing import AsyncIterator

import websockets

from ..config import DEEPGRAM_API_KEY, STT_DEBUG, STT_LANGUAGE

_log = logging.getLogger("moly-voice")

_SR = 16000
_URL = "wss://api.deepgram.com/v1/listen"


class DeepgramStream:
    """한 발화(턴) 동안 열리는 Deepgram 스트림."""

    name = "deepgram"  # 측정·로그 라벨

    def __init__(self, model: str = "nova-3") -> None:
        self.model = model
        self._ws: websockets.WebSocketClientProtocol | None = None
        # 진단용 송신 통계
        self._sent_frames = 0
        self._sent_bytes = 0
        self._peak = 0  # int16 절댓값 피크(전부 0이면 무음)

    async def open(self) -> None:
        # endpointing: 발화 도중 멈춤마다 부분 final을 미리 확정 → 버튼 end 때 flush가 빨라짐(속도용).
        # 턴 경계는 여전히 버튼(end). 중간에 final이 쪼개져도 누적해서 합치므로 무방.
        qs = (
            f"model={self.model}&encoding=linear16&sample_rate={_SR}&channels=1"
            f"&interim_results=true&smart_format=true&endpointing=300&language={STT_LANGUAGE}"
        )
        url = f"{_URL}?{qs}"
        hdrs = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}
        try:  # websockets 신버전(additional_headers) / 구버전(extra_headers) 호환
            self._ws = await websockets.connect(url, additional_headers=hdrs, max_size=None)
        except TypeError:
            self._ws = await websockets.connect(url, extra_headers=hdrs, max_size=None)

    async def send_audio(self, pcm: bytes) -> None:
        if not self._ws:
            return
        # int16 절댓값 피크로 무음 판별(전부 0이면 peak=0 → 마이크/포맷 문제)
        a = array.array("h")
        a.frombytes(pcm[: len(pcm) // 2 * 2])
        if a:
            self._peak = max(self._peak, max(abs(x) for x in a))
        self._sent_frames += 1
        self._sent_bytes += len(pcm)
        await self._ws.send(pcm)  # 실패 시 예외 → 호출부(main.py)에서 로깅

    async def finalize(self) -> None:
        """버튼 end → 남은 오디오 최종화 요청(이후 results()가 Metadata 보고 종료)."""
        _log.info("DG sent: frames=%d bytes=%d peak=%d(/32767)",
                  self._sent_frames, self._sent_bytes, self._peak)
        if self._ws:
            await self._ws.send(json.dumps({"type": "CloseStream"}))

    async def results(self) -> AsyncIterator[tuple[str, bool]]:
        """(transcript, is_final) 스트림. Metadata(최종화 완료) 보면 종료."""
        if not self._ws:
            return
        async for raw in self._ws:
            msg = json.loads(raw)
            t = msg.get("type")
            # Results 외(Metadata/Error/UtteranceEnd 등)는 항상, Results는 STT_DEBUG일 때만 raw 노출
            if STT_DEBUG or t != "Results":
                _log.info("DG raw[%s]: %s", t, raw[:300])
            if t == "Results":
                alt = (msg.get("channel", {}).get("alternatives") or [{}])[0]
                txt = alt.get("transcript", "")
                if STT_DEBUG:
                    _log.info("DG Results: is_final=%s txt=%r", msg.get("is_final"), txt)
                if txt:
                    yield txt, bool(msg.get("is_final"))
            elif t == "Metadata":
                _log.info("DG Metadata duration=%.2fs channels=%s",
                          msg.get("duration", -1), msg.get("channels"))
                break

    async def close(self) -> None:
        if self._ws:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None
