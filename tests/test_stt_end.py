"""end 핸들러 4결과 검증 — no_speech/timeout/stt_error에 final(result)+turn_end+idle.

가짜 STTStream을 주입해 결과별 경로를 강제한다. 핵심: 빈음성/타임아웃일 때도 서버가
'끝났다'(turn_end+idle)를 보내 클라가 안 멈추는지(과거 버그) 확인.
"""
import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("DEEPGRAM_API_KEY", "x")
os.environ.setdefault("ELEVENLABS_API_KEY", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starlette.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402

results = []


def check(name, cond):
    results.append((name, cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


class FakeSTT:
    """results()를 모드별로: recognized=final 1개, no_speech=없음, timeout=hang."""
    name = "fake"
    mode = "no_speech"

    async def open(self):
        pass

    async def send_audio(self, pcm):
        pass

    async def finalize(self):
        pass

    async def results(self):
        if FakeSTT.mode == "recognized":
            yield "hello world", True
        elif FakeSTT.mode == "timeout":
            await asyncio.sleep(5)  # grace보다 길게 → wait_for 타임아웃
        # no_speech: 아무것도 yield 안 함
        if False:  # async generator로 만들기 위한 더미 yield
            yield

    async def close(self):
        pass


async def fake_run_turn(send_json, send_bytes, messages, transcript, user_id="x", memory=""):
    messages.append({"role": "user", "content": transcript})
    await send_json({"type": "status", "state": "thinking"})
    await send_json({"type": "reply_delta", "text": "ok"})
    messages.append({"role": "assistant", "content": "ok"})
    await send_json({"type": "turn_end"})
    await send_json({"type": "status", "state": "idle"})


async def fake_load(uid="x"):
    return ""


async def fake_commit(*a, **k):
    pass


main.create_stt_stream = lambda: FakeSTT()
main.run_turn = fake_run_turn
main.load_memory = fake_load
main.commit_memory = fake_commit


def collect_until_idle(ws, mx=14):
    out = []
    for _ in range(mx):
        e = ws.receive_json()
        out.append(e)
        if e.get("type") == "status" and e.get("state") == "idle":
            break
    return out


def _voice_turn(client, mode):
    FakeSTT.mode = mode
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "session_init", "history": []})
        ws.receive_json()  # ready
        ws.send_json({"type": "start", "sampleRate": 16000})
        ws.send_json({"type": "end"})
        return collect_until_idle(ws)


def run():
    client = TestClient(main.app)

    # ── no_speech: final(no_speech) + turn_end + idle ──
    ev = _voice_turn(client, "no_speech")
    types = [(e.get("type"), e.get("result"), e.get("state")) for e in ev]
    check("no_speech: final(result=no_speech, text='')",
          any(e.get("type") == "transcript" and e.get("final") and e.get("result") == "no_speech"
              and e.get("text") == "" for e in ev))
    check("no_speech: turn_end 있음", any(e.get("type") == "turn_end" for e in ev))
    check("no_speech: 마지막 idle(멈춤 해소)",
          ev[-1].get("type") == "status" and ev[-1].get("state") == "idle")

    # ── timeout: final(timeout) + turn_end + idle ──
    orig_grace = main.STT_FINALIZE_GRACE_MS
    main.STT_FINALIZE_GRACE_MS = 300  # 빠른 타임아웃
    try:
        ev = _voice_turn(client, "timeout")
    finally:
        main.STT_FINALIZE_GRACE_MS = orig_grace
    check("timeout: final(result=timeout)",
          any(e.get("type") == "transcript" and e.get("result") == "timeout" for e in ev))
    check("timeout: turn_end + idle",
          any(e.get("type") == "turn_end" for e in ev)
          and ev[-1].get("state") == "idle")

    # ── recognized: no_speech 분기로 안 빠지고 run_turn 진행 ──
    ev = _voice_turn(client, "recognized")
    check("recognized: transcript(result=recognized)",
          any(e.get("type") == "transcript" and e.get("result") == "recognized" for e in ev))
    check("recognized: run_turn 진행(thinking)",
          any(e.get("type") == "status" and e.get("state") == "thinking" for e in ev))
    check("recognized: no_speech final 안 보냄",
          not any(e.get("result") in ("no_speech", "timeout", "stt_error") for e in ev))

    print()
    passed = sum(1 for _, c in results if c)
    print(f"=== {passed}/{len(results)} PASS ===")
    return passed == len(results)


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
