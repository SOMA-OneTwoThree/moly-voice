"""환경설정 — .env 또는 OS 환경변수."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


DEEPGRAM_API_KEY = env("DEEPGRAM_API_KEY")
ELEVENLABS_API_KEY = env("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = env("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ELEVENLABS_MODEL = env("ELEVENLABS_MODEL", "eleven_flash_v2_5")
STT_LANGUAGE = env("STT_LANGUAGE", "multi")
# 버튼 end 후 Deepgram finalize(최종 transcript) 완료를 기다리는 상한(ms).
# flush 왕복 전체를 덮어야 하므로 한 홉 RTT보다 넉넉히. 짧으면 정상 발화가 timeout→빈 결과로 회귀.
STT_FINALIZE_GRACE_MS = int(env("STT_FINALIZE_GRACE_MS", "2000") or "2000")
# STT 진단 상세 로그 토글(프레임 단위 raw 로깅 등). 평시 0, 디버깅 시 1.
STT_DEBUG = env("STT_DEBUG", "0") not in ("0", "", "false", "False")
LLM_URL = env("LLM_URL", "http://localhost:3000/api/chat")
# 캐시 워밍(prefetch) 엔드포인트 — /chat 와 같은 호스트의 /warm.
WARM_URL = env("WARM_URL", LLM_URL.replace("/chat", "/warm"))

# 데모 고정 user_id(인증 전). Mem0 장기기억이 이 id로 쌓인다.
DEMO_USER_ID = env("DEMO_USER_ID", "molly_voice_demo")
