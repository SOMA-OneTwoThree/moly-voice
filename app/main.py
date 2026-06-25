"""moly-voice — 실시간 음성 게이트웨이 (push-to-talk 데모). hello!

웹 ↔ /ws(WebSocket): 오디오 in/out + 제어 이벤트.
한 턴: 버튼 start → 마이크 PCM16 16k 프레임 → Deepgram STT → 버튼 end → finalize
       → moly-llm /chat → 문장분할 → ElevenLabs TTS(PCM24k) → 웹 재생.
barge-in: SPEAKING 중 start(또는 interrupt) → 진행 턴 cancel.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState

from .config import STT_FINALIZE_GRACE_MS
from .feedback import request_feedback
from .gateway.orchestrator import run_turn
from .memory import commit_memory, load_memory
from .stt.deepgram import DeepgramStream

_log = logging.getLogger("moly-voice")
_log.setLevel(logging.INFO)  # STT 진단 로그(transcript/타임아웃/연결·인증)가 기본 WARNING에 묻히지 않게

app = FastAPI(title="moly-voice")

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "moly-voice"}


async def _pump_stt(dg: DeepgramStream, send_json) -> str:
    """Deepgram 결과를 읽어 interim은 표시, final은 누적 → 최종 transcript 반환."""
    finals: list[str] = []
    last_interim = ""
    try:
        async for txt, is_final in dg.results():
            if is_final:
                finals.append(txt)
            else:
                last_interim = txt
                await send_json({"type": "transcript", "text": txt, "final": False})
    except Exception as e:  # noqa: BLE001  (CancelledError는 BaseException → orphan cancel은 통과)
        _log.warning("STT pump error: %r", e)  # 연결 종료/프로토콜/인증 등 원인 타입 노출
    transcript = " ".join(finals).strip() or last_interim.strip()
    if transcript:  # 빈 transcript는 클라이언트로 보내지 않음(불필요 final 이벤트/잠재 크래시점 제거)
        await send_json({"type": "transcript", "text": transcript, "final": True})
    return transcript


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    memory_text = await load_memory()  # 세션시작 장기기억 1회 로드(세션 내내 고정). fail-safe → ""
    send_lock = asyncio.Lock()  # 동시 send 직렬화(Starlette WS는 concurrent send 비안전)

    async def _safe_send(do_send) -> None:
        async with send_lock:
            if ws.application_state != WebSocketState.CONNECTED:
                return  # 이미 닫힘 → 조용히 스킵(백그라운드 태스크가 예외로 죽지 않게)
            try:
                await do_send()
            except RuntimeError as e:  # 상태 통과 후 전송 중 닫힘("Cannot call send once a close...")
                _log.debug("ws send skipped (closed): %s", e)

    async def send_json(d: dict) -> None:
        await _safe_send(lambda: ws.send_json(d))

    async def send_bytes(b: bytes) -> None:
        await _safe_send(lambda: ws.send_bytes(b))

    messages: list[dict] = []
    dg: DeepgramStream | None = None
    stt_task: asyncio.Task | None = None
    turn_task: asyncio.Task | None = None
    rx_frames = rx_bytes = dropped = 0  # 턴별 인바운드 오디오 진단 카운터

    async def cancel_turn() -> None:
        nonlocal turn_task
        if turn_task and not turn_task.done():
            turn_task.cancel()
            try:
                await turn_task
            except asyncio.CancelledError:
                pass
        turn_task = None

    async def cancel_stt() -> None:
        """진행 중인 STT 스트림/태스크 정리 — orphan 방지 + dg.close 일원화."""
        nonlocal dg, stt_task
        if stt_task and not stt_task.done():
            stt_task.cancel()
            try:
                await stt_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if dg:
            await dg.close()
        dg, stt_task = None, None

    async def safe_turn(transcript: str) -> None:
        try:
            await run_turn(send_json, send_bytes, messages, transcript, memory=memory_text)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            for d in ({"type": "error", "message": str(e)[:200]},
                      {"type": "status", "state": "idle"}):
                try:
                    await send_json(d)
                except Exception:  # noqa: BLE001
                    pass

    async def do_feedback() -> None:
        """'교정 받기' — 현재까지의 대화로 교정 요청 후 결과 전송(블로킹 회피용 태스크)."""
        try:
            result = await request_feedback(messages)
            await send_json({"type": "feedback", "data": result})
        except Exception as e:  # noqa: BLE001
            await send_json({"type": "feedback_error", "message": str(e)[:200]})

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break

            audio = msg.get("bytes")
            if audio is not None:
                rx_frames += 1
                rx_bytes += len(audio)
                if dg:
                    try:
                        await dg.send_audio(audio)
                    except Exception as e:  # noqa: BLE001  # 기존 무조건 pass → 가시화
                        _log.warning("DG send_audio 실패: %r", e)
                else:
                    dropped += 1  # dg.open() 완료 전 도착 → 드롭(EC2 지연 시 증가)
                continue

            text = msg.get("text")
            if not text:
                continue
            evt = json.loads(text)
            t = evt.get("type")

            if t == "start":
                await cancel_turn()  # barge-in: 진행 중 발화 중단
                await cancel_stt()   # 직전 턴 잔여 STT 정리(중복 start 대비, None이면 no-op)
                rx_frames = rx_bytes = dropped = 0  # 새 턴 진단 카운터 리셋
                sr = evt.get("sampleRate")
                if sr and sr != 16000:
                    _log.warning("브라우저 AudioContext sampleRate=%s ≠ Deepgram 16000 — 포맷 불일치 가능", sr)
                else:
                    _log.info("mic sampleRate=%s", sr)
                dg = DeepgramStream()
                try:
                    await dg.open()
                except Exception as e:  # noqa: BLE001  # 인증/DNS/연결 실패 — 명확 로그 + 통지 후 복귀
                    _log.exception("STT open 실패(인증/연결 확인)")
                    dg = None
                    await send_json({"type": "error", "message": f"STT 연결 실패: {e}"[:200]})
                    await send_json({"type": "status", "state": "idle"})
                    continue
                await send_json({"type": "status", "state": "listening"})
                stt_task = asyncio.create_task(_pump_stt(dg, send_json))

            elif t == "end":
                _log.info("RX audio: frames=%d bytes=%d dropped(before dg)=%d",
                          rx_frames, rx_bytes, dropped)
                if dg and stt_task:
                    await dg.finalize()
                    try:  # grace: 최종 transcript가 그 안에 오면 즉시 진행
                        transcript = await asyncio.wait_for(
                            stt_task, timeout=STT_FINALIZE_GRACE_MS / 1000)
                    except asyncio.TimeoutError:  # 타임아웃 = 지연/네트워크
                        transcript = ""
                        _log.warning("STT grace timeout: %dms 내 finalize 미도착(지연/네트워크 의심)",
                                     STT_FINALIZE_GRACE_MS)
                    except Exception:  # noqa: BLE001  # 그 외 = 연결/인증 등 — 전체 트레이스백 노출
                        transcript = ""
                        _log.exception("STT finalize 실패(연결/인증 등)")
                    await cancel_stt()  # 성공·타임아웃 양쪽에서 orphan 방지 + dg.close 일원화
                    if transcript.strip():  # 정상 → 받은 값 로그 후 LLM 진행
                        _log.info("STT transcript: %r", transcript)
                        turn_task = asyncio.create_task(safe_turn(transcript))
                    else:  # 빈 결과 → 경고 + LLM 호출 스킵
                        _log.warning("STT 빈 결과 — LLM 호출 스킵")

            elif t == "text_turn":  # 채팅 입력 — STT 건너뛰고 텍스트를 바로 턴으로
                msg_text = (evt.get("text") or "").strip()
                if not msg_text:
                    continue
                await cancel_turn()  # barge-in: 진행 중 응답 중단
                await cancel_stt()   # 마이크 열려있으면 정리
                _log.info("text_turn: %r", msg_text)
                turn_task = asyncio.create_task(safe_turn(msg_text))

            elif t == "request_feedback":  # '교정 받기' — 전체 히스토리 교정(논블로킹)
                asyncio.create_task(do_feedback())

            elif t == "interrupt":
                await cancel_turn()
                await send_json({"type": "status", "state": "idle"})
    finally:
        await cancel_stt()   # 리스닝 중 disconnect 시 orphan stt_task 정리(근본 원인 해소)
        await cancel_turn()
        await commit_memory(messages)  # 세션종료 → transcript를 mem0에 커밋(fail-safe, 빈 세션 스킵)


# 데모 정적 페이지 서빙(/). 라우트(/health,/ws) 정의 후 마지막에 마운트.
if _WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")
