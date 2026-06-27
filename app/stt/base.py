"""STT provider 추상 인터페이스 (턴 단위 스트림).

한 발화(턴) 동안 열리는 스트림. 푸시투토크: open → send_audio× → finalize → results 소비 → close.
provider별 차이(바이너리 vs base64-JSON, finalize 방식, 결과 메시지 포맷)는 각 구현이 흡수하고,
게이트웨이(main.py)는 이 인터페이스만 본다.
"""
from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable


@runtime_checkable
class STTStream(Protocol):
    """한 턴 동안 열리는 STT 스트림."""

    async def open(self) -> None:
        """provider WS 연결 + 세션 시작."""
        ...

    async def send_audio(self, pcm: bytes) -> None:
        """PCM16 16k mono 프레임 전송(구현이 인코딩 흡수)."""
        ...

    async def finalize(self) -> None:
        """버튼 end → 남은 오디오 최종화 요청(이후 results가 final 내고 종료)."""
        ...

    def results(self) -> AsyncIterator[tuple[str, bool]]:
        """(transcript, is_final) 스트림. final 누적은 호출부(_pump_stt) 담당."""
        ...

    async def close(self) -> None:
        """스트림 정리."""
        ...
