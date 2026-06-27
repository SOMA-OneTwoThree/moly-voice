"""STT provider 팩토리 — STT_PROVIDER env로 턴 스트림 생성.

main.py는 이 팩토리만 호출하고 구체 provider를 모른다. 한 턴마다 새 스트림 인스턴스.
"""
from __future__ import annotations

import logging

from ..config import STT_PROVIDER
from .base import STTStream

_log = logging.getLogger("moly-voice")


def create_stt_stream() -> STTStream:
    """STT_PROVIDER에 따른 새 STT 스트림. 알 수 없는 값이면 deepgram 폴백."""
    provider = STT_PROVIDER.lower()
    if provider == "elevenlabs":
        from .elevenlabs_scribe import ElevenLabsScribeStream  # 지연 import(미구현 시 deepgram만 로드)

        return ElevenLabsScribeStream()
    if provider == "soniox":
        from .soniox import SonioxStream

        return SonioxStream()
    if provider not in ("deepgram", ""):
        _log.warning("알 수 없는 STT_PROVIDER=%r → deepgram 폴백", STT_PROVIDER)
    from .deepgram import DeepgramStream

    return DeepgramStream()
