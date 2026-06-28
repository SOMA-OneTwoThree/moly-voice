"""moly-voice — 실시간 음성 게이트웨이 (push-to-talk 데모). hello!

웹 ↔ /ws(WebSocket): 오디오 in/out + 제어 이벤트.
한 턴: 버튼 start → 마이크 PCM16 16k 프레임 → Deepgram STT → 버튼 end → finalize
       → moly-llm /chat → 문장분할 → ElevenLabs TTS(PCM24k) → 웹 재생.
barge-in: SPEAKING 중 start(또는 interrupt) → 진행 턴 cancel.

세션 경계 = 사용자 의도(시작/종료 버튼)지 WS 연결 수명이 아님. 한 세션이 WS 연결
여러 개에 걸칠 수 있다(네트워크 끊김→재연결). 그래서:
- history 소유 = 클라(앱). 연결/재연결마다 session_init으로 게이트웨이에 시드.
- 게이트웨이는 무상태: 받은 history로 그 연결 동안만 누적, 끊기면 버림(클라가 보관).
- mem0 커밋 = 명시적 end_session(종료 버튼)일 때만. WS 끊김(네트워크)으론 커밋 안 함.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState

from .alerts import alert
from .auth import verify_token
from .config import (
    DEMO_USER_ID,
    MAX_HISTORY_BYTES,
    MAX_HISTORY_ITEMS,
    MAX_TEXT_LEN,
    MAX_TURNS_PER_MIN,
    REQUIRE_AUTH,
    SESSION_INIT_TIMEOUT_S,
    STT_FINALIZE_GRACE_MS,
    STT_PROVIDER,
)
from .feedback import request_feedback
from .gateway.orchestrator import run_turn
from .memory import commit_memory, load_memory
from .profile import fetch_nickname
from .stt.base import STTStream
from .stt.factory import create_stt_stream

_log = logging.getLogger("moly-voice")
_log.setLevel(logging.INFO)  # STT 진단 로그(transcript/타임아웃/연결·인증)가 기본 WARNING에 묻히지 않게

app = FastAPI(title="moly-voice")

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "moly-voice"}


async def _pump_stt(dg: STTStream, send_json) -> str:
    """Deepgram 결과를 읽어 interim은 표시, final은 누적 → 최종 transcript 반환."""
    finals: list[str] = []
    last_interim = ""
    try:
        async for txt, is_final in dg.results():
            if is_final:
                finals.append(txt)
            else:
                last_interim = txt
                # 화면 interim = 확정 세그먼트(finals) + 현재 interim(txt) 누적.
                # (endpointing으로 final이 조각나도 전체 문장으로 보이게)
                await send_json({"type": "transcript",
                                 "text": " ".join([*finals, txt]).strip(), "final": False})
    except Exception as e:  # noqa: BLE001  (CancelledError는 BaseException → orphan cancel은 통과)
        _log.warning("STT pump error: %r", e)  # 연결 종료/프로토콜/인증 등 원인 타입 노출
    transcript = " ".join(finals).strip() or last_interim.strip()
    if transcript:  # 빈 transcript는 클라이언트로 보내지 않음(불필요 final 이벤트/잠재 크래시점 제거)
        await send_json({"type": "transcript", "text": transcript, "final": True, "result": "recognized"})
    return transcript


def _bearer_from_header(ws: WebSocket) -> str:
    """`Authorization: Bearer <token>` 헤더의 토큰 문자열. 없으면 ""."""
    parts = (ws.headers.get("authorization") or "").split()
    return parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else ""


async def _user_from_header(ws: WebSocket) -> str | None:
    """헤더 토큰 검증 → user_id. 없거나 무효면 None.

    네이티브/iOS는 토큰을 이 헤더로 보낸다(계약). 브라우저는 헤더를 못 붙여 session_init 폴백.
    """
    token = _bearer_from_header(ws)
    return await verify_token(token) if token else None


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    # 인증: 헤더 토큰을 연결 시 검증해 user_id 확정. REQUIRE_AUTH인데 유효 헤더 토큰이 없으면
    # accept 전에 핸드셰이크를 거부한다(클라는 연결 실패=인증 오류로 처리). 헤더 없는 웹은
    # accept 후 session_init.token으로 폴백(REQUIRE_AUTH=false 환경에서만).
    header_uid = await _user_from_header(ws)
    if REQUIRE_AUTH and not header_uid:
        await ws.close(code=1008)  # 미인증 → 핸드셰이크 거부
        return
    await ws.accept()
    # 신원: 헤더로 확정됐으면 그 user_id, 아니면 session_init 폴백에서 결정.
    user_id = header_uid or DEMO_USER_ID
    auth_via_header = header_uid is not None  # True면 session_init에서 재인증 안 함
    memory_text = ""             # session_init에서 load_memory로 채움(세션 내내 고정)
    committed = False            # end_session(명시 종료) 시에만 True — 네트워크 끊김과 구분
    authed = False               # session_init 성공 전엔 어떤 턴도 처리 안 함(인증 게이트)
    turn_times: deque[float] = deque()  # 턴 타임스탬프(분당 레이트 제한)
    send_lock = asyncio.Lock()  # 동시 send 직렬화(Starlette WS는 concurrent send 비안전)

    def turn_allowed() -> bool:
        """연결당 분당 턴 상한. 초과면 False(턴 생성 차단)."""
        now = time.monotonic()
        while turn_times and now - turn_times[0] > 60:
            turn_times.popleft()
        if len(turn_times) >= MAX_TURNS_PER_MIN:
            return False
        turn_times.append(now)
        return True

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
    dg: STTStream | None = None
    stt_task: asyncio.Task | None = None
    turn_task: asyncio.Task | None = None
    fb_task: asyncio.Task | None = None  # 교정 요청(논블로킹) — 종료 시 정리
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
            await run_turn(send_json, send_bytes, messages, transcript,
                           user_id=user_id, memory=memory_text)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            await alert(repr(e), context="run_turn 실패")
            # 클라엔 일반 메시지만(예외 상세 노출 금지, A6)
            for d in ({"type": "error", "message": "응답 생성 중 문제가 생겼어요."},
                      {"type": "status", "state": "idle"}):
                try:
                    await send_json(d)
                except Exception:  # noqa: BLE001
                    pass

    async def do_feedback() -> None:
        """'교정 받기' — 현재까지의 대화로 교정 요청 후 결과 전송(블로킹 회피용 태스크)."""
        try:
            result = await request_feedback(messages, user_id)
            await send_json({"type": "feedback", "data": result})
        except Exception as e:  # noqa: BLE001
            await alert(repr(e), context="feedback 실패")
            await send_json({"type": "feedback_error", "message": "교정을 불러오지 못했어요."})

    try:
        while True:
            # 미인증 동안은 idle 타임아웃 — session_init 안 보내고 자원만 점유하는 연결 차단.
            if not authed:
                try:
                    msg = await asyncio.wait_for(ws.receive(),
                                                 timeout=SESSION_INIT_TIMEOUT_S)
                except asyncio.TimeoutError:
                    await send_json({"type": "error", "message": "세션 초기화 시간 초과"})
                    break
            else:
                msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break

            audio = msg.get("bytes")
            if audio is not None:
                if not authed:
                    continue  # 인증 전 오디오는 드롭(자원 소비 차단)
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
            try:  # malformed JSON 한 건이 세션을 끊지 않도록 방어(B3)
                evt = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                _log.warning("malformed JSON 프레임 무시")
                continue
            if not isinstance(evt, dict):
                continue
            t = evt.get("type")

            # 인증 게이트: session_init 성공 전엔 어떤 턴/제어도 처리 안 함(A2).
            if t != "session_init" and not authed:
                await send_json({"type": "error",
                                 "message": "세션이 초기화되지 않았습니다 (session_init 먼저)"})
                continue

            if t == "session_init":
                # 연결/재연결 시드 — 클라가 보관한 이번 세션 history로 게이트웨이를 초기화.
                # 재연결이면 history에 누적분이 옴 → 대화 끊김 없이 이어짐. 빈 배열이면 새 세션.
                # 인증: 헤더로 이미 user_id가 확정됐으면(iOS/네이티브) 재인증하지 않는다 —
                # iOS는 토큰을 session_init에 싣지 않는다. 헤더 인증이 안 된 경우(브라우저)만
                # session_init.token 폴백(REQUIRE_AUTH=false 환경에서만 여기 도달). 평문 user_id 불신.
                if not auth_via_header:
                    token = (evt.get("token") or "").strip()
                    if token:
                        uid = await verify_token(token)
                        if not uid:
                            await send_json({"type": "auth_error",
                                             "message": "invalid or expired token"})
                            break  # 인증 실패 → 연결 종료
                        user_id = uid
                    else:
                        user_id = DEMO_USER_ID  # 헤더·토큰 둘 다 없음(로컬/데모)
                # history 크기 제한(A3/A5) — 역할도 user/assistant로만 제한(B5).
                hist = evt.get("history") or []
                clean: list[dict] = []
                total = 0
                for m in hist:
                    if not (isinstance(m, dict) and m.get("role") in ("user", "assistant")):
                        continue
                    content = m.get("content")
                    if not isinstance(content, str) or not content:
                        continue
                    total += len(content)
                    if len(clean) >= MAX_HISTORY_ITEMS or total > MAX_HISTORY_BYTES:
                        _log.warning("history 상한 초과 — 절단(items=%d bytes=%d)", len(clean), total)
                        break
                    clean.append({"role": m["role"], "content": content})
                messages.clear()
                messages.extend(clean)
                committed = False  # 재연결로 새 WS면 이전 커밋여부 리셋(아직 종료 안 함)
                # 장기기억(mem0) + 닉네임(profiles)을 동시에 로드(연결 지연 최소화).
                # 닉네임은 mem0 밖에서 통제된 형태로 memory_text 끝에 주입 → 첫 턴부터 LLM이 이름 앎.
                prof_token = _bearer_from_header(ws) or (evt.get("token") or "").strip()
                memory_text, nickname = await asyncio.gather(
                    load_memory(user_id),                  # fail-safe → ""
                    fetch_nickname(prof_token, user_id),   # fail-safe → None
                )
                if nickname:
                    memory_text = f"{memory_text}\n\n[사용자 정보]\n이름: {nickname}".strip()
                authed = True  # 이제부터 턴 처리 허용
                await send_json({"type": "ready", "provider": STT_PROVIDER})  # 클라 측정 라벨

            elif t == "start":
                await cancel_turn()  # barge-in: 진행 중 발화 중단
                await cancel_stt()   # 직전 턴 잔여 STT 정리(중복 start 대비, None이면 no-op)
                rx_frames = rx_bytes = dropped = 0  # 새 턴 진단 카운터 리셋
                sr = evt.get("sampleRate")
                if sr and sr != 16000:
                    _log.warning("브라우저 AudioContext sampleRate=%s ≠ Deepgram 16000 — 포맷 불일치 가능", sr)
                else:
                    _log.info("mic sampleRate=%s", sr)
                dg = create_stt_stream()
                try:
                    await dg.open()
                except Exception as e:  # noqa: BLE001  # 인증/DNS/연결 실패 — 로그+알림 후 복귀
                    _log.exception("STT open 실패(인증/연결 확인)")
                    await alert(repr(e), context="STT open 실패")
                    dg = None
                    # 클라엔 일반 메시지만(내부 URL/예외 상세 노출 금지, A6)
                    await send_json({"type": "error", "message": "STT 연결에 실패했어요."})
                    await send_json({"type": "status", "state": "idle"})
                    continue
                await send_json({"type": "status", "state": "listening"})
                stt_task = asyncio.create_task(_pump_stt(dg, send_json))

            elif t == "end":
                _log.info("RX audio: frames=%d bytes=%d dropped(before dg)=%d",
                          rx_frames, rx_bytes, dropped)
                if dg and stt_task:
                    provider = getattr(dg, "name", STT_PROVIDER)
                    t_fin = time.monotonic()  # A/B 측정: finalize→최종 transcript
                    await dg.finalize()
                    # 결과 판정: recognized(정상) / no_speech(빈값) / timeout / stt_error.
                    # 모든 경우 최종 이벤트를 정확히 한 번 + turn_end + idle 보장(클라 멈춤 방지).
                    result = "recognized"
                    try:  # grace: 최종 transcript가 그 안에 오면 즉시 진행
                        transcript = await asyncio.wait_for(
                            stt_task, timeout=STT_FINALIZE_GRACE_MS / 1000)
                        if not transcript.strip():
                            result = "no_speech"
                    except asyncio.TimeoutError:  # 타임아웃 = 지연/네트워크
                        transcript = ""
                        result = "timeout"
                        _log.warning("STT grace timeout: %dms 내 finalize 미도착(지연/네트워크 의심)",
                                     STT_FINALIZE_GRACE_MS)
                    except Exception:  # noqa: BLE001  # 그 외 = 연결/인증 등 — 전체 트레이스백 노출
                        transcript = ""
                        result = "stt_error"
                        _log.exception("STT finalize 실패(연결/인증 등)")
                    stt_ms = int((time.monotonic() - t_fin) * 1000)  # STT꼬리(A/B 핵심지표)
                    await cancel_stt()  # 성공·타임아웃 양쪽에서 orphan 방지 + dg.close 일원화
                    if result == "recognized":  # 길이만 로그(원문 비기록, C3) 후 LLM 진행
                        _log.info("STT[%s] finalize→final %dms (len=%d)",
                                  provider, stt_ms, len(transcript))
                        # 최종 transcript(recognized)는 _pump_stt가 이미 전송함.
                        if not turn_allowed():  # 분당 턴 상한(A3)
                            await send_json({"type": "error", "message": "잠시 후 다시 시도해 주세요."})
                            await send_json({"type": "status", "state": "idle"})
                        else:
                            turn_task = asyncio.create_task(safe_turn(transcript))  # run_turn이 turn_end+idle
                    else:  # no_speech/timeout/stt_error — AI 응답 없이 턴 종료(클라 idle 복귀)
                        _log.info("STT[%s] %s %dms", provider, result, stt_ms)
                        await send_json({"type": "transcript", "text": "",
                                         "final": True, "result": result})
                        await send_json({"type": "turn_end"})
                        await send_json({"type": "status", "state": "idle"})

            elif t == "text_turn":  # 채팅 입력 — STT 건너뛰고 텍스트를 바로 턴으로
                msg_text = (evt.get("text") or "").strip()
                if not msg_text:
                    continue
                if len(msg_text) > MAX_TEXT_LEN:  # 입력 크기 제한(A3/A5)
                    await send_json({"type": "error", "message": "메시지가 너무 길어요."})
                    continue
                if not turn_allowed():  # 분당 턴 상한(A3)
                    await send_json({"type": "error", "message": "잠시 후 다시 시도해 주세요."})
                    continue
                await cancel_turn()  # barge-in: 진행 중 응답 중단
                await cancel_stt()   # 마이크 열려있으면 정리
                _log.info("text_turn 수신(len=%d)", len(msg_text))  # 원문 비기록(C3)
                turn_task = asyncio.create_task(safe_turn(msg_text))

            elif t == "request_feedback":  # 연결 끊기 전 교정(논블로킹) — 종료 시 finally가 정리
                if fb_task and not fb_task.done():
                    fb_task.cancel()
                fb_task = asyncio.create_task(do_feedback())

            elif t == "end_session":  # 명시적 종료(종료 버튼) — 이때만 mem0 커밋
                # 네트워크 끊김(WS만 닫힘)과 구분되는 유일한 지점. 멱등(중복 종료 방지).
                if not committed:
                    committed = True
                    await commit_memory(messages, user_id)
                await send_json({"type": "session_committed"})

            elif t == "interrupt":
                await cancel_turn()
                await send_json({"type": "status", "state": "idle"})
    finally:
        # WS 끊김 = 정리만. mem0 커밋은 여기서 하지 않는다 — 네트워크 끊김(의도치 않은 종료)에도
        # finally가 돌기 때문. 커밋은 명시적 end_session에서만. 끊김이 진짜 종료면 클라가
        # end_session을 먼저 보낸 뒤 닫으므로 그때 이미 커밋됨. history는 클라가 보관(재시드).
        await cancel_stt()   # 리스닝 중 disconnect 시 orphan stt_task 정리(근본 원인 해소)
        await cancel_turn()
        if fb_task and not fb_task.done():  # 교정 미완 상태로 끊김 → orphan httpx 정리
            fb_task.cancel()
            try:
                await fb_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


# 데모 정적 페이지 서빙(/). 라우트(/health,/ws) 정의 후 마지막에 마운트.
if _WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")
