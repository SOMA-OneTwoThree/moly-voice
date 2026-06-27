"""ElevenLabs Scribe 어댑터 — 프로토콜 매핑 검증(네트워크 없이 fake WS).

send_audio/ finalize의 송신 JSON, results의 partial/committed 파싱·종료조건을 확인.
실제 ElevenLabs 호출은 e2e(별도)로.
"""
import asyncio
import base64
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("ELEVENLABS_API_KEY", "x")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.stt.base import STTStream  # noqa: E402
from app.stt.elevenlabs_scribe import ElevenLabsScribeStream  # noqa: E402

results = []


def check(name, cond):
    results.append((name, cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


class FakeWS:
    def __init__(self, incoming=None):
        self.sent = []
        self._incoming = list(incoming or [])
        self.closed = False

    async def send(self, s):
        self.sent.append(s)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._incoming:
            raise StopAsyncIteration
        return self._incoming.pop(0)

    async def close(self):
        self.closed = True


async def main():
    check("프로토콜 적합성(STTStream)", isinstance(ElevenLabsScribeStream(), STTStream))

    # send_audio → input_audio_chunk + base64 + commit:false
    s = ElevenLabsScribeStream()
    s._ws = FakeWS()
    pcm = b"\x01\x02\x03\x04"
    await s.send_audio(pcm)
    msg = json.loads(s._ws.sent[0])
    check("send_audio: message_type", msg["message_type"] == "input_audio_chunk")
    check("send_audio: base64 일치", msg["audio_base_64"] == base64.b64encode(pcm).decode())
    check("send_audio: commit False", msg["commit"] is False)
    check("send_audio: sample_rate 16000", msg["sample_rate"] == 16000)

    # finalize → commit:true + 빈 오디오
    await s.finalize()
    fin = json.loads(s._ws.sent[1])
    check("finalize: commit True", fin["commit"] is True)
    check("finalize: 빈 오디오", fin["audio_base_64"] == "")

    # results: partial×2 → committed → 종료(committed 뒤 메시지는 소비 안 함)
    incoming = [
        json.dumps({"message_type": "session_started", "session_id": "x"}),
        json.dumps({"message_type": "partial_transcript", "text": "how"}),
        json.dumps({"message_type": "partial_transcript", "text": "how are"}),
        json.dumps({"message_type": "committed_transcript", "text": "how are you"}),
        json.dumps({"message_type": "partial_transcript", "text": "SHOULD NOT READ"}),
    ]
    s2 = ElevenLabsScribeStream()
    s2._ws = FakeWS(incoming)
    out = [pair async for pair in s2.results()]
    check("results: (interim, interim, final) 순서",
          out == [("how", False), ("how are", False), ("how are you", True)])
    # committed에서 break → 그 뒤 메시지는 소비 안 되고 남아있어야 정상
    check("results: committed 뒤 종료(추가 미소비)",
          len(s2._ws._incoming) == 1
          and "SHOULD NOT READ" in s2._ws._incoming[0])

    # results: 에러 메시지 → 종료(yield 없음)
    s3 = ElevenLabsScribeStream()
    s3._ws = FakeWS([json.dumps({"message_type": "auth_error", "text": ""})])
    out3 = [pair async for pair in s3.results()]
    check("results: 에러 → 빈 결과·종료", out3 == [])

    print()
    passed = sum(1 for _, c in results if c)
    print(f"=== {passed}/{len(results)} PASS ===")
    return passed == len(results)


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
