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


async def run_turn(
    send_json: SendJson,
    send_bytes: SendBytes,
    messages: list[dict],
    transcript: str,
    memory: str = "",
) -> None:
    """messages를 in-place로 갱신(user/assistant 추가)하며 한 턴을 처리. memory=세션 고정분."""
    messages.append({"role": "user", "content": transcript})
    await send_json({"type": "status", "state": "thinking"})

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

    async for delta in stream_reply(messages, DEMO_USER_ID, memory=memory):
        reply_parts.append(delta)
        await send_json({"type": "reply_delta", "text": delta})
        for sentence in splitter.feed(delta):
            await speak(sentence)
    for sentence in splitter.flush():
        await speak(sentence)

    messages.append({"role": "assistant", "content": "".join(reply_parts)})
    await send_json({"type": "turn_end"})
    await send_json({"type": "status", "state": "idle"})
