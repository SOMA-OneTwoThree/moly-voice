"""STTProvider 추상화 검증 — 팩토리 분기 + 프로토콜 적합성(회귀 안전).

실제 STT 호출 없음(인스턴스 생성·인터페이스 적합성만).
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("DEEPGRAM_API_KEY", "x")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.stt import factory  # noqa: E402
from app.stt.base import STTStream  # noqa: E402
from app.stt.deepgram import DeepgramStream  # noqa: E402

results = []


def check(name, cond):
    results.append((name, cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


def run():
    # 기본(deepgram) → DeepgramStream + 프로토콜 만족
    s = factory.create_stt_stream()
    check("기본 provider → DeepgramStream", isinstance(s, DeepgramStream))
    check("DeepgramStream이 STTStream 만족", isinstance(s, STTStream))

    # 5개 메서드 다 있음(인터페이스 충실성)
    for m in ("open", "send_audio", "finalize", "results", "close"):
        check(f"메서드 존재: {m}", hasattr(s, m))

    # 알 수 없는 provider → deepgram 폴백(크래시 없음)
    orig = factory.STT_PROVIDER
    try:
        factory.STT_PROVIDER = "bogus-xyz"
        s2 = factory.create_stt_stream()
        check("알 수 없는 provider → deepgram 폴백", isinstance(s2, DeepgramStream))
    finally:
        factory.STT_PROVIDER = orig

    # 언어 기본이 en
    from app.config import STT_LANGUAGE
    check("STT_LANGUAGE 기본 en", STT_LANGUAGE == "en")

    # provider 라벨(측정용)
    check("DeepgramStream.name=deepgram", s.name == "deepgram")
    from app.stt.elevenlabs_scribe import ElevenLabsScribeStream
    check("ElevenLabsScribeStream.name=elevenlabs",
          ElevenLabsScribeStream().name == "elevenlabs")

    print()
    passed = sum(1 for _, c in results if c)
    print(f"=== {passed}/{len(results)} PASS ===")
    return passed == len(results)


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
