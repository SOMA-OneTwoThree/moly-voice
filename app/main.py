"""moly-voice — 실시간 음성 게이트웨이 (push-to-talk 데모).

웹 ↔ /ws(WebSocket): 오디오 in/out + 제어 이벤트.
한 턴: 버튼 start → 마이크 PCM16 16k 프레임 → Deepgram STT → 버튼 end → finalize
       → moly-llm /chat → 문장분할 → ElevenLabs TTS(PCM24k) → 웹 재생.
barge-in: SPEAKING 중 start(또는 interrupt) → 진행 턴 cancel.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles

from .gateway.orchestrator import run_turn
from .stt.deepgram import DeepgramStream

app = FastAPI(title="moly-voice")

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "moly-voice"}


async def _pump_stt(dg: DeepgramStream, send_json) -> str:
    """Deepgram 결과를 읽어 interim은 표시, final은 누적 → 최종 transcript 반환."""
    finals: list[str] = []
    async for txt, is_final in dg.results():
        if is_final:
            finals.append(txt)
        else:
            await send_json({"type": "transcript", "text": txt, "final": False})
    transcript = " ".join(finals).strip()
    await send_json({"type": "transcript", "text": transcript, "final": True})
    return transcript


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    send_lock = asyncio.Lock()  # 동시 send 직렬화(Starlette WS는 concurrent send 비안전)

    async def send_json(d: dict) -> None:
        async with send_lock:
            await ws.send_json(d)

    async def send_bytes(b: bytes) -> None:
        async with send_lock:
            await ws.send_bytes(b)

    messages: list[dict] = []
    dg: DeepgramStream | None = None
    stt_task: asyncio.Task | None = None
    turn_task: asyncio.Task | None = None

    async def cancel_turn() -> None:
        nonlocal turn_task
        if turn_task and not turn_task.done():
            turn_task.cancel()
            try:
                await turn_task
            except asyncio.CancelledError:
                pass
        turn_task = None

    async def safe_turn(transcript: str) -> None:
        try:
            await run_turn(send_json, send_bytes, messages, transcript)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            for d in ({"type": "error", "message": str(e)[:200]},
                      {"type": "status", "state": "idle"}):
                try:
                    await send_json(d)
                except Exception:  # noqa: BLE001
                    pass

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break

            audio = msg.get("bytes")
            if audio is not None:
                if dg:
                    try:
                        await dg.send_audio(audio)
                    except Exception:  # noqa: BLE001
                        pass
                continue

            text = msg.get("text")
            if not text:
                continue
            evt = json.loads(text)
            t = evt.get("type")

            if t == "start":
                await cancel_turn()  # barge-in: 진행 중 발화 중단
                dg = DeepgramStream()
                await dg.open()
                await send_json({"type": "status", "state": "listening"})
                stt_task = asyncio.create_task(_pump_stt(dg, send_json))

            elif t == "end":
                if dg and stt_task:
                    await dg.finalize()
                    try:
                        transcript = await asyncio.wait_for(stt_task, timeout=10)
                    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                        transcript = ""
                    await dg.close()
                    dg, stt_task = None, None
                    if transcript.strip():
                        turn_task = asyncio.create_task(safe_turn(transcript))

            elif t == "interrupt":
                await cancel_turn()
                await send_json({"type": "status", "state": "idle"})
    finally:
        if dg:
            await dg.close()
        await cancel_turn()


# 데모 정적 페이지 서빙(/). 라우트(/health,/ws) 정의 후 마지막에 마운트.
if _WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")
