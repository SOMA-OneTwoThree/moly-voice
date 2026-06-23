"""델타 스트림 → 문장 경계 분할 (harness에서 그대로 포팅).

LLM delta를 누적하다 종결부호를 만나면 한 문장 방출 → 첫 문장 즉시 TTS 시작(지연↓).
"""
from __future__ import annotations

from typing import Iterator

_SENTENCE_ENDERS = set(".!?…。！？\n")


class SentenceSplitter:
    def __init__(self, min_chars: int = 1) -> None:
        self._buf = ""
        self._min = min_chars

    def feed(self, delta: str) -> Iterator[str]:
        self._buf += delta
        while True:
            idx = next((i for i, ch in enumerate(self._buf) if ch in _SENTENCE_ENDERS), -1)
            if idx == -1:
                break
            sentence = self._buf[: idx + 1].strip()
            self._buf = self._buf[idx + 1 :]
            if len(sentence) >= self._min:
                yield sentence

    def flush(self) -> Iterator[str]:
        tail = self._buf.strip()
        self._buf = ""
        if len(tail) >= self._min:
            yield tail
