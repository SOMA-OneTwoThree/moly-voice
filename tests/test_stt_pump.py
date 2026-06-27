"""_pump_stt 검증 — interim 누적 표시 + 최종 transcript (NameError 회귀 방지).

이 테스트는 'current_interim' 같은 미정의 변수 버그를 잡는다(첫 interim에서 NameError가
나면 except에 먹혀 누적이 끊김 → 아래 단언 실패).
"""
import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("DEEPGRAM_API_KEY", "x")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.main import _pump_stt  # noqa: E402

results = []


def check(name, cond):
    results.append((name, cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


class FakeStream:
    def __init__(self, seq):
        self._seq = seq

    async def results(self):
        for txt, fin in self._seq:
            yield txt, fin


async def main():
    sent = []

    async def send_json(d):
        sent.append(d)

    # interim "how" → interim "how are" → final "how are you" → interim "today"
    seq = [("how", False), ("how are", False), ("how are you", True), ("today", False)]
    transcript = await _pump_stt(FakeStream(seq), send_json)

    interims = [s["text"] for s in sent if not s["final"]]
    finals_sent = [s["text"] for s in sent if s["final"]]

    # interim은 누적(확정분 + 현재 interim)으로 표시 — 첫 interim에서 안 끊겨야 전부 옴
    check("interim 누적 표시(전부 처리)",
          interims == ["how", "how are", "how are you today"])
    # 최종 transcript = 확정분 join
    check("최종 transcript = 확정분", transcript == "how are you")
    check("최종 transcript 1회 전송", finals_sent == ["how are you"])

    print()
    passed = sum(1 for _, c in results if c)
    print(f"=== {passed}/{len(results)} PASS ===")
    return passed == len(results)


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
