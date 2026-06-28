"""orchestrator 실로직 검증 — stream_reply/TTS만 mock, run_turn·run_greeting_turn 직접 실행.

session_protocol 테스트는 run_greeting_turn을 fake로 대체하므로 convo 구성·append 규칙·
이벤트 순서는 거기서 검증되지 않는다. 여기서 실제 코드 경로를 돌린다.
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

from app.gateway import orchestrator  # noqa: E402

# ── mock: LLM 스트림 + TTS ──
captured: dict = {}


def fake_stream_factory(deltas):
    async def fake_stream_reply(convo, user_id, memory=""):
        captured["convo"] = [dict(m) for m in convo]  # 진입 시점 convo 스냅샷
        captured["user_id"] = user_id
        captured["memory"] = memory
        for d in deltas:
            yield d
    return fake_stream_reply


async def fake_synthesize_stream(text):
    captured.setdefault("spoken", []).append(text)
    yield b"\x00\x01"  # 더미 오디오 1프레임


def collect():
    events = []
    audio = []

    async def send_json(d):
        events.append(d)

    async def send_bytes(b):
        audio.append(b)

    return events, audio, send_json, send_bytes


def run():
    results = []

    def check(name, cond):
        results.append((name, cond))
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")

    orchestrator.synthesize_stream = fake_synthesize_stream

    # ── 1) 새 세션 선발화: convo=[합성 user 지시] 1건, history엔 assistant만 남음 ──
    captured.clear()
    orchestrator.stream_reply = fake_stream_factory(["Hey there. ", "Good to see you."])
    events, audio, sj, sb = collect()
    messages = []
    asyncio.run(orchestrator.run_greeting_turn(sj, sb, messages, user_id="u1", memory="MEM"))

    convo = captured["convo"]
    check("1a convo 1건(합성 user 지시만)", len(convo) == 1)
    check("1b convo[0] role=user", convo[0]["role"] == "user")
    check("1c 지시문은 GREETING_INSTRUCTION", convo[0]["content"] == orchestrator.GREETING_INSTRUCTION)
    check("1d user_id 전달", captured["user_id"] == "u1")
    check("1e memory 전달", captured["memory"] == "MEM")
    # history엔 assistant 인사만(합성 user 지시는 안 남음)
    check("1f messages에 assistant 1건만", messages == [{"role": "assistant", "content": "Hey there. Good to see you."}])
    types = [e.get("type") for e in events]
    # 실제 순서: reply_delta(델타 도착)가 먼저, 그 델타가 문장 완성 시 speaking 붙음(run_turn과 동일).
    check("1g 이벤트: thinking→reply_delta→speaking→reply_delta→turn_end→idle",
          types == ["status", "reply_delta", "status", "reply_delta", "turn_end", "status"])
    check("1h status 상태 순서", [e["state"] for e in events if e["type"] == "status"] == ["thinking", "speaking", "idle"])
    check("1i TTS 호출됨(오디오 송신)", len(audio) >= 1)

    # ── 2) 기존 history 있을 때 선발화: 지시문이 history 뒤에 붙고 history는 LLM에만, 안 오염 ──
    captured.clear()
    orchestrator.stream_reply = fake_stream_factory(["Welcome back."])
    events, audio, sj, sb = collect()
    messages = [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "reply"}]
    asyncio.run(orchestrator.run_greeting_turn(sj, sb, messages, user_id="u1"))
    convo = captured["convo"]
    check("2a convo=기존2 + 지시1", len(convo) == 3 and convo[-1]["content"] == orchestrator.GREETING_INSTRUCTION)
    check("2b messages엔 지시문 안 남고 assistant 추가(총3)",
          messages == [{"role": "user", "content": "earlier"},
                       {"role": "assistant", "content": "reply"},
                       {"role": "assistant", "content": "Welcome back."}])

    # ── 3) 빈 응답 선발화: phantom assistant 안 남김(다음 턴 정합성) ──
    captured.clear()
    orchestrator.stream_reply = fake_stream_factory([])  # LLM이 아무것도 안 줌
    events, audio, sj, sb = collect()
    messages = []
    asyncio.run(orchestrator.run_greeting_turn(sj, sb, messages, user_id="u1"))
    check("3a 빈 응답이면 messages 비어있음(phantom 없음)", messages == [])
    check("3b 빈 응답도 turn_end+idle 보장",
          [e.get("type") for e in events][-2:] == ["turn_end", "status"])

    # ── 4) 회귀: run_turn은 user+assistant 둘 다 append(기존 계약 유지) ──
    captured.clear()
    orchestrator.stream_reply = fake_stream_factory(["Hi!"])
    events, audio, sj, sb = collect()
    messages = []
    asyncio.run(orchestrator.run_turn(sj, sb, messages, "hello", user_id="u1", memory="M"))
    check("4a run_turn convo에 user 발화 포함", captured["convo"][-1] == {"role": "user", "content": "hello"})
    check("4b run_turn messages=user+assistant",
          messages == [{"role": "user", "content": "hello"},
                       {"role": "assistant", "content": "Hi!"}])

    print()
    passed = sum(1 for _, c in results if c)
    print(f"=== {passed}/{len(results)} PASS ===")
    return passed == len(results)


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
