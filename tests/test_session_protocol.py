"""세션 생애주기 WS 프로토콜 검증 (외부 의존 mock).

핵심 계약:
1. session_init → 클라 history로 messages 시드 + load_memory(user_id) + "ready"
2. end_session(명시 종료) 일 때만 commit_memory 1회
3. 네트워크 끊김(end_session 없이 WS만 닫힘) → commit_memory 호출 안 함
4. 재연결 = 새 WS + session_init(누적 history) → messages 재시드(대화 이어짐)
5. 멱등: end_session 두 번 → commit 한 번

실 LLM/STT/TTS/mem0 호출 없이 게이트웨이 제어흐름만 검증한다.
"""
import os
import sys
from pathlib import Path

# config import 전 더미 환경(키 없어도 모듈 로드되게)
os.environ.setdefault("DEEPGRAM_API_KEY", "x")
os.environ.setdefault("ELEVENLABS_API_KEY", "x")
os.environ.setdefault("ANTHROPIC_API_KEY", "x")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starlette.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402

# ── 외부 의존 mock + 호출 기록 ──
load_calls: list[str] = []
commit_calls: list[dict] = []
run_calls: list[dict] = []


async def fake_load_memory(user_id="molly_voice_demo"):
    load_calls.append(user_id)
    return f"MEM:{user_id}"


async def fake_commit_memory(messages, user_id="molly_voice_demo"):
    commit_calls.append({"user_id": user_id, "messages": [dict(m) for m in messages]})


async def fake_request_feedback(messages, user_id="molly_voice_demo"):
    return {"has_corrections": False, "corrections": []}


_TOKENS = {"tok-u1": "u1", "tok-u2": "u2"}  # 유효 토큰→user.id, 그 외=무효


async def fake_verify_token(token):
    return _TOKENS.get(token)  # 무효면 None(거절)


async def fake_run_turn(send_json, send_bytes, messages, transcript,
                        user_id="molly_voice_demo", memory=""):
    # 실 run_turn과 동일하게 messages를 in-place 갱신 + 이벤트 송신
    run_calls.append({
        "transcript": transcript, "user_id": user_id, "memory": memory,
        "history_in": [dict(m) for m in messages],  # 진입 시점(시드 확인용)
    })
    messages.append({"role": "user", "content": transcript})
    await send_json({"type": "reply_delta", "text": "ok"})
    messages.append({"role": "assistant", "content": f"reply:{transcript}"})
    await send_json({"type": "turn_end"})


greet_calls: list[dict] = []


async def fake_run_greeting(send_json, send_bytes, messages,
                           user_id="molly_voice_demo", memory=""):
    # 실 run_greeting_turn과 동일: 합성 user는 안 남기고 assistant 인사만 messages에 추가.
    greet_calls.append({
        "user_id": user_id, "memory": memory,
        "history_in": [dict(m) for m in messages],
    })
    await send_json({"type": "reply_delta", "text": "hey there"})
    messages.append({"role": "assistant", "content": "hey there"})
    await send_json({"type": "turn_end"})


main.load_memory = fake_load_memory
main.commit_memory = fake_commit_memory
main.request_feedback = fake_request_feedback
main.run_turn = fake_run_turn
main.run_greeting_turn = fake_run_greeting
main.verify_token = fake_verify_token
main.GREETING_ENABLED = False  # 기존 세션 테스트는 선발화 off(빈 history 시 추가 트래픽 방지)


def _drain_turn(ws):
    """text_turn 후 reply_delta + turn_end 소비."""
    assert ws.receive_json()["type"] == "reply_delta"
    assert ws.receive_json()["type"] == "turn_end"


def run():
    client = TestClient(main.app)
    results = []

    def check(name, cond):
        results.append((name, cond))
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")

    # ── 1) session_init 시드 + ready + load_memory(user_id) ──
    load_calls.clear(); commit_calls.clear(); run_calls.clear()
    with client.websocket_connect("/ws") as ws:
        seed = [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"}]
        ws.send_json({"type": "session_init", "token": "tok-u1", "history": seed})
        ready = ws.receive_json()
        check("1a session_init(토큰)→ready", ready.get("type") == "ready")
        check("1a' ready에 provider 라벨", "provider" in ready)
        check("1b 토큰→user.id 도출 load_memory(u1)", load_calls == ["u1"])

        ws.send_json({"type": "text_turn", "text": "how are you"})
        _drain_turn(ws)
        check("1c run_turn이 시드 history 봄(2건)",
              run_calls[-1]["history_in"] == seed)
        check("1d run_turn user_id 전달", run_calls[-1]["user_id"] == "u1")
        check("1e run_turn memory 전달", run_calls[-1]["memory"] == "MEM:u1")

        # ── 2) end_session → commit 1회(시드2 + 이번턴 user/assistant = 4) ──
        ws.send_json({"type": "end_session"})
        check("2a session_committed", ws.receive_json() == {"type": "session_committed"})
        check("2b commit 1회", len(commit_calls) == 1)
        check("2c commit에 전체 4건", len(commit_calls[0]["messages"]) == 4)
        check("2d commit user_id=u1", commit_calls[0]["user_id"] == "u1")

    # ── 3) 네트워크 끊김: end_session 없이 닫힘 → commit 호출 없어야 ──
    load_calls.clear(); commit_calls.clear(); run_calls.clear()
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "session_init", "history": []})
        ws.receive_json()  # ready
        ws.send_json({"type": "text_turn", "text": "mid sentence"})
        _drain_turn(ws)
        # end_session 안 보내고 컨텍스트 종료 = 소켓만 끊김(네트워크 드롭)
    check("3a 끊김엔 commit 안 함", len(commit_calls) == 0)

    # ── 4) 재연결: 누적 history로 session_init → 재시드(이어감) ──
    load_calls.clear(); commit_calls.clear(); run_calls.clear()
    accumulated = [
        {"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"}, {"role": "assistant", "content": "d"},
    ]
    with client.websocket_connect("/ws") as ws:  # 새 WS = 재연결
        ws.send_json({"type": "session_init", "token": "tok-u2", "history": accumulated})
        ws.receive_json()  # ready
        ws.send_json({"type": "text_turn", "text": "continue"})
        _drain_turn(ws)
        check("4a 재연결 후 run_turn이 누적 4건 봄",
              run_calls[-1]["history_in"] == accumulated)
        ws.send_json({"type": "end_session"})
        ws.receive_json()
        check("4b 이어진 세션 commit 6건",
              len(commit_calls[0]["messages"]) == 6)

    # ── 5) 멱등: end_session 두 번 → commit 한 번 ──
    load_calls.clear(); commit_calls.clear(); run_calls.clear()
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "session_init", "history": []})
        ws.receive_json()
        ws.send_json({"type": "text_turn", "text": "x"})
        _drain_turn(ws)
        ws.send_json({"type": "end_session"}); ws.receive_json()
        ws.send_json({"type": "end_session"}); ws.receive_json()
    check("5 중복 end_session→commit 1회", len(commit_calls) == 1)

    # ── 6) 무효 토큰 → auth_error + 연결 거절(ready 안 옴) ──
    load_calls.clear(); commit_calls.clear()
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "session_init", "token": "bad", "history": []})
        msg = ws.receive_json()
        check("6a 무효 토큰→auth_error", msg.get("type") == "auth_error")
    check("6b 무효 토큰 거절(load 안 함)", load_calls == [])

    # ── 7) 토큰 없음(데모/로컬) → DEMO_USER_ID 폴백 ──
    load_calls.clear()
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "session_init", "history": []})  # 토큰 생략
        check("7a 토큰 없음→ready", ws.receive_json().get("type") == "ready")
        check("7b DEMO_USER_ID 폴백", load_calls == [main.DEMO_USER_ID])

    # ── 8) 인증 게이트(A2): session_init 전 text_turn → 거절, 턴 생성 안 함 ──
    run_calls.clear()
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "text_turn", "text": "hi"})  # session_init 없이
        msg = ws.receive_json()
        check("8a 미인증 턴 거절", msg.get("type") == "error")
        check("8b 미인증 run_turn 안 함", run_calls == [])

    # ── 9) REQUIRE_AUTH=true + 헤더/토큰 없음 → 연결 자체 거부(accept 전) ──
    main.REQUIRE_AUTH = True
    load_calls.clear()
    try:
        rejected = False
        try:
            with client.websocket_connect("/ws") as ws:  # 헤더 없음 → 핸드셰이크 거부
                ws.receive_json()
        except Exception:
            rejected = True
        check("9 REQUIRE_AUTH=true 미인증→연결 거부", rejected)
        check("9b 거부 시 load 안 함", load_calls == [])
    finally:
        main.REQUIRE_AUTH = False

    # ── 10) 입력 크기(A3): text_turn 길이 초과 → 거절 ──
    main.MAX_TEXT_LEN = 5
    run_calls.clear()
    try:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "session_init", "history": []})
            ws.receive_json()
            ws.send_json({"type": "text_turn", "text": "이건너무길다"})
            check("10a 긴 text_turn 거절", ws.receive_json().get("type") == "error")
            check("10b 거절 시 run_turn 안 함", run_calls == [])
    finally:
        main.MAX_TEXT_LEN = 4000

    # ── 11) history 상한(A3): 항목수 초과 → 절단 ──
    main.MAX_HISTORY_ITEMS = 2
    run_calls.clear()
    try:
        big = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
               for i in range(6)]
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "session_init", "history": big})
            ws.receive_json()
            ws.send_json({"type": "text_turn", "text": "x"})
            _drain_turn(ws)
            check("11 history 2건으로 절단", len(run_calls[-1]["history_in"]) == 2)
    finally:
        main.MAX_HISTORY_ITEMS = 200

    # ── 12) 턴 레이트(A3): 분당 상한 초과 → 거절 ──
    main.MAX_TURNS_PER_MIN = 2
    try:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "session_init", "history": []})
            ws.receive_json()
            for _ in range(2):
                ws.send_json({"type": "text_turn", "text": "hi"})
                _drain_turn(ws)
            ws.send_json({"type": "text_turn", "text": "hi"})  # 3번째 = 초과
            check("12 턴 레이트 초과 거절", ws.receive_json().get("type") == "error")
    finally:
        main.MAX_TURNS_PER_MIN = 30

    # ── 13) malformed JSON(B3): 무시하고 연결 유지 ──
    with client.websocket_connect("/ws") as ws:
        ws.send_text("this is not json {{{")  # 깨진 프레임
        ws.send_json({"type": "session_init", "history": []})  # 그 다음 정상
        check("13 깨진 JSON 무시 후 정상 동작", ws.receive_json().get("type") == "ready")

    # ── 14) 헤더 인증(iOS): Authorization Bearer 유효 → user_id 헤더에서, session_init 토큰 없이 ──
    load_calls.clear()
    with client.websocket_connect("/ws", headers={"Authorization": "Bearer tok-u1"}) as ws:
        ws.send_json({"type": "session_init", "history": []})  # 토큰 없음(iOS처럼)
        check("14a 헤더 인증→ready", ws.receive_json().get("type") == "ready")
        check("14b 헤더 user_id로 load(u1)", load_calls == ["u1"])

    # ── 15) 헤더 유효 + session_init에 다른 토큰 → 헤더 우선(session_init 토큰 무시) ──
    load_calls.clear()
    with client.websocket_connect("/ws", headers={"Authorization": "Bearer tok-u1"}) as ws:
        ws.send_json({"type": "session_init", "token": "tok-u2", "history": []})
        ws.receive_json()
        check("15 헤더 우선(session_init 토큰 무시)", load_calls == ["u1"])

    # ── 16) REQUIRE_AUTH=true + 헤더 무효 → 연결 거부 ──
    main.REQUIRE_AUTH = True
    load_calls.clear()
    try:
        rejected = False
        try:
            with client.websocket_connect("/ws", headers={"Authorization": "Bearer bad"}) as ws:
                ws.receive_json()
        except Exception:
            rejected = True
        check("16 REQUIRE_AUTH+무효 헤더→거부", rejected)

        # ── 17) REQUIRE_AUTH=true + 헤더 유효 → 연결됨 ──
        with client.websocket_connect("/ws", headers={"Authorization": "Bearer tok-u1"}) as ws:
            ws.send_json({"type": "session_init", "history": []})
            check("17 REQUIRE_AUTH+유효 헤더→ready", ws.receive_json().get("type") == "ready")
            check("17b 헤더 user_id로 load(u1)", load_calls == ["u1"])
    finally:
        main.REQUIRE_AUTH = False

    # ── 18) 닉네임 주입: fetch_nickname 결과가 memory_text 끝에 통제된 블록으로 붙음 ──
    async def fake_nick(token, user_id):
        return "철수"

    orig_nick = main.fetch_nickname
    main.fetch_nickname = fake_nick
    run_calls.clear()
    try:
        with client.websocket_connect("/ws", headers={"Authorization": "Bearer tok-u1"}) as ws:
            ws.send_json({"type": "session_init", "history": []})
            ws.receive_json()  # ready
            ws.send_json({"type": "text_turn", "text": "hi"})
            _drain_turn(ws)
        mem = run_calls[-1]["memory"]
        check("18a 닉네임이 memory_text에 주입", "이름: 철수" in mem)
        check("18b 기존 memory(mem0)도 유지", mem.startswith("MEM:u1"))
    finally:
        main.fetch_nickname = orig_nick

    # ── 19) 닉네임 None(미온보딩/실패) → memory_text 그대로 ──
    async def fake_nick_none(token, user_id):
        return None

    main.fetch_nickname = fake_nick_none
    run_calls.clear()
    try:
        with client.websocket_connect("/ws", headers={"Authorization": "Bearer tok-u1"}) as ws:
            ws.send_json({"type": "session_init", "history": []})
            ws.receive_json()
            ws.send_json({"type": "text_turn", "text": "hi"})
            _drain_turn(ws)
        check("19 닉네임 없으면 주입 안 함", run_calls[-1]["memory"] == "MEM:u1")
    finally:
        main.fetch_nickname = orig_nick

    # ── 20) 선발화(GREETING_ENABLED): 새 세션이면 Moly가 먼저 인사, 재연결엔 안 함 ──
    main.GREETING_ENABLED = True
    try:
        # 20a 새 세션(빈 history) → ready 직후 인사 턴(reply_delta+turn_end)
        greet_calls.clear()
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "session_init", "history": []})
            check("20a 새 세션→ready", ws.receive_json().get("type") == "ready")
            check("20b 선발화 reply_delta", ws.receive_json().get("type") == "reply_delta")
            check("20c 선발화 turn_end", ws.receive_json().get("type") == "turn_end")
        check("20d 선발화 1회 발동", len(greet_calls) == 1)
        check("20e 선발화 진입 시 history 비어있음", greet_calls[0]["history_in"] == [])

        # 20f 재연결(누적 history) → 선발화 안 함, 곧장 정상 턴 가능
        greet_calls.clear()
        accumulated = [{"role": "user", "content": "a"},
                       {"role": "assistant", "content": "b"}]
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "session_init", "history": accumulated})
            check("20f 재연결→ready", ws.receive_json().get("type") == "ready")
            ws.send_json({"type": "text_turn", "text": "hi again"})
            _drain_turn(ws)
        check("20g 재연결엔 선발화 안 함", len(greet_calls) == 0)
    finally:
        main.GREETING_ENABLED = False

    print()
    passed = sum(1 for _, c in results if c)
    print(f"=== {passed}/{len(results)} PASS ===")
    return passed == len(results)


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
