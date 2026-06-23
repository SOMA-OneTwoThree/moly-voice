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
LLM_URL = env("LLM_URL", "http://localhost:3000/api/chat")

# 데모 고정 user_id(인증 전). Mem0 장기기억이 이 id로 쌓인다.
DEMO_USER_ID = env("DEMO_USER_ID", "molly_voice_demo")
