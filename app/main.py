"""moly-voice — 실시간 음성 게이트웨이.

앱과 WebSocket으로 오디오를 주고받으며 한 턴을 오케스트레이션한다:
  앱 오디오 → STT(Deepgram) → ② moly-llm(SSE) → TTS(ElevenLabs) → 앱 오디오
세션 대화(messages[])를 메모리에 보관하고, 턴마다 ① moly-server에 async 영속.
턴테이킹/barge-in 처리. 컨테이너(영속 WebSocket) — 서버리스 불가.

스캐폴드: /health + /ws 골격만. 구현은 gateway/stt/tts 모듈에 채운다.
참고: moly-pipeline-test/voice-harness 의 stt·tts·pipeline 코드 재사용.
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

app = FastAPI(title="moly-voice")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "moly-voice"}


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    # TODO: session_token 검증(① server) → 히스토리·user_id 로드
    #       → gateway.orchestrator 로 STT→LLM→TTS 턴 루프 + barge-in
    await websocket.accept()
    try:
        while True:
            await websocket.receive()  # 오디오(바이너리) / 제어(JSON)
    except WebSocketDisconnect:
        return
