"""한 턴 오케스트레이션: transcript → LLM → 문장분할 → TTS → 클라이언트 오디오.

interrupt(barge-in)는 이 코루틴을 task로 감싸 cancel() 하면 처리된다.
"""
from __future__ import annotations

import re
from typing import Awaitable, Callable

from ..config import DEMO_USER_ID
from ..llm import stream_reply
from ..shared.splitter import SentenceSplitter
from ..tts.elevenlabs import synthesize_stream

SendJson = Callable[[dict], Awaitable[None]]
SendBytes = Callable[[bytes], Awaitable[None]]

# TTS 안전: 음성으로 읽으면 어색한 기호 제거/치환(em-dash·마크다운 등).
_STRIP = re.compile(r"[*_`#>]")
_DASH = re.compile(r"\s*[—–]\s*")


def _tts_safe(text: str) -> str:
    text = _DASH.sub(", ", text)
    text = _STRIP.sub("", text)
    return text.strip()


# 선발화 지시 — LLM 호출에만 싣는 합성 user 메시지(히스토리/클라/메모리엔 남기지 않음).
# 톤·관계·기억 활용 원칙은 system 프롬프트의 [Speaking First] 섹션이 소유(single source).
# 여기선 "지금 먼저 말하라"는 트리거만 — 두 소스 중복/모순 방지(슬림 트리거).
GREETING_INSTRUCTION = (
    "[The conversation is just starting and the person hasn't said anything yet. "
    "Speak first now, following your Speaking First guidance.]"
)

# moly-llm renderer의 PASSIVE 헤더와 동기화(app/memory/renderer.py _PASSIVE_HEADER).
# 선발화에선 이 블록 이후(PASSIVE 기억)를 입력에서 제외 → Moly가 첫 턴에 배경/민감 기억을
# 먼저 꺼내지 않게 한다(프롬프트 [Speaking First]의 "don't surface background first"와 이중 방어).
# 헤더 형식이 드리프트하면 안전쪽(원본 그대로 = 더 포함)으로 동작.
_PASSIVE_HEADER_PREFIX = "[Background"


def _active_only_memory(memory: str) -> str:
    """선발화용 — PASSIVE(Background) 블록을 잘라낸 ACTIVE 기억만. 헤더 없으면 원본 그대로."""
    lines = memory.splitlines()
    cut = next((i for i, ln in enumerate(lines)
                if ln.startswith(_PASSIVE_HEADER_PREFIX)), None)
    return memory if cut is None else "\n".join(lines[:cut]).strip()


async def _stream_reply_to_client(
    send_json: SendJson,
    send_bytes: SendBytes,
    convo: list[dict],
    user_id: str,
    memory: str,
) -> str:
    """convo로 LLM 스트리밍 → reply_delta 송신 + 문장단위 TTS. 전체 응답 텍스트를 반환."""
    splitter = SentenceSplitter()
    reply_parts: list[str] = []
    speaking = False

    async def speak(sentence: str) -> None:
        nonlocal speaking
        clean = _tts_safe(sentence)
        if not clean:
            return
        if not speaking:
            await send_json({"type": "status", "state": "speaking"})
            speaking = True
        async for chunk in synthesize_stream(clean):
            await send_bytes(chunk)

    async for delta in stream_reply(convo, user_id, memory=memory):
        reply_parts.append(delta)
        await send_json({"type": "reply_delta", "text": delta})
        for sentence in splitter.feed(delta):
            await speak(sentence)
    for sentence in splitter.flush():
        await speak(sentence)
    return "".join(reply_parts)


async def run_turn(
    send_json: SendJson,
    send_bytes: SendBytes,
    messages: list[dict],
    transcript: str,
    user_id: str = DEMO_USER_ID,
    memory: str = "",
) -> None:
    """messages를 in-place로 갱신(user/assistant 추가)하며 한 턴을 처리. memory=세션 고정분."""
    messages.append({"role": "user", "content": transcript})
    await send_json({"type": "status", "state": "thinking"})
    reply = await _stream_reply_to_client(send_json, send_bytes, messages, user_id, memory)
    messages.append({"role": "assistant", "content": reply})
    await send_json({"type": "turn_end"})
    await send_json({"type": "status", "state": "idle"})


async def run_greeting_turn(
    send_json: SendJson,
    send_bytes: SendBytes,
    messages: list[dict],
    user_id: str = DEMO_USER_ID,
    memory: str = "",
) -> None:
    """유저 입력 없이 Moly가 먼저 인사하는 턴(선발화). 합성 지시는 LLM 호출에만 쓰고
    히스토리엔 assistant 인사만 남긴다 — 지시문은 클라/메모리에 노출되지 않는다.

    새 세션에서만 호출(messages 비어있음). 만일 history가 있어도 안전하게 동작한다.
    """
    convo = [*messages, {"role": "user", "content": GREETING_INSTRUCTION}]
    await send_json({"type": "status", "state": "thinking"})
    # 선발화엔 ACTIVE 기억만 — PASSIVE(배경/민감)를 첫 턴에 먼저 꺼내지 않게(코드 가드).
    reply = await _stream_reply_to_client(
        send_json, send_bytes, convo, user_id, _active_only_memory(memory))
    if reply:  # 빈 응답이면 phantom assistant 턴(다음 턴 정합성 깨뜨림)을 남기지 않음
        messages.append({"role": "assistant", "content": reply})
    await send_json({"type": "turn_end"})
    await send_json({"type": "status", "state": "idle"})
