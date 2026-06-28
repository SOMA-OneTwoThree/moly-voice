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
# STT provider 선택: deepgram(기본) | elevenlabs(Scribe v2 RT) | soniox. 한 턴마다 팩토리가 생성.
STT_PROVIDER = env("STT_PROVIDER", "deepgram")
# 영어회화 앱 — 언어 고정 en(이전 기본 "multi"에서 변경). provider 공통.
STT_LANGUAGE = env("STT_LANGUAGE", "en")
# 버튼 end 후 Deepgram finalize(최종 transcript) 완료를 기다리는 상한(ms).
# flush 왕복 전체를 덮어야 하므로 한 홉 RTT보다 넉넉히. 짧으면 정상 발화가 timeout→빈 결과로 회귀.
STT_FINALIZE_GRACE_MS = int(env("STT_FINALIZE_GRACE_MS", "2000") or "2000")
# STT 진단 상세 로그 토글(프레임 단위 raw 로깅 등). 평시 0, 디버깅 시 1.
STT_DEBUG = env("STT_DEBUG", "0") not in ("0", "", "false", "False")
LLM_URL = env("LLM_URL", "http://localhost:8000/chat")
# 장기기억 엔드포인트 — /chat 와 같은 베이스의 /memory/*.
# LLM_URL의 "/chat"을 치환해 파생(기존 컨벤션). LLM_URL이 moly-llm 실경로(.../chat)를
# 가리켜야 정상 파생됨. 인프라에서 MEMORY_LOAD_URL/MEMORY_COMMIT_URL 명시 override 가능.
_LLM_BASE = LLM_URL.replace("/chat", "")
MEMORY_LOAD_URL = env("MEMORY_LOAD_URL", f"{_LLM_BASE}/memory/load")
MEMORY_COMMIT_URL = env("MEMORY_COMMIT_URL", f"{_LLM_BASE}/memory/commit")
# 교정 — '교정 받기' 시 게이트웨이가 호출(브라우저는 moly-llm 직접 접근 불가).
FEEDBACK_URL = env("FEEDBACK_URL", f"{_LLM_BASE}/feedback")

# 세션시작 메모리 로드 타임아웃(초). mem0 get_all이 콜드 커넥션/스파이크로 5s를 종종 넘겨
# ReadTimeout으로 메모리를 못 불러오던 문제 → 여유 상향. 로드는 ready 전에 await되므로 너무
# 키우면 세션 시작이 느려짐(균형 12s). 텔레메트리 off(moly-llm)로 평소 지연은 줄어듦.
MEMORY_LOAD_TIMEOUT_S = float(env("MEMORY_LOAD_TIMEOUT_S", "12") or "12")

# 데모 고정 user_id(인증 전/토큰 없을 때 폴백). Mem0 장기기억이 이 id로 쌓인다.
DEMO_USER_ID = env("DEMO_USER_ID", "molly_voice_demo")

# WS 인증 — Supabase access token 검증용(server require-user.ts와 동일: remote getUser).
# 둘 다 공개값(anon 키는 브라우저에도 실림). server와 같은 값이어야 함.
SUPABASE_URL = env("SUPABASE_URL")
SUPABASE_ANON_KEY = env("SUPABASE_ANON_KEY")

# 인증 강제 토글. true면 유효 토큰 없는 연결 거절(프로덕션). false면 DEMO 폴백 허용(로컬/데모).
# 프론트 로그인 붙어 토큰 전송 시작하면 프로덕션 env에서 true로 켠다.
REQUIRE_AUTH = env("REQUIRE_AUTH", "false").lower() in ("1", "true", "yes")

# 선발화 토글. true(기본)면 새 세션(빈 history) 시작 시 Moly가 유저 입력을 기다리지 않고
# 먼저 인사 턴을 연다. 재연결(누적 history)에선 발동 안 함(중복 인사 방지). 끄려면 false.
GREETING_ENABLED = env("GREETING_ENABLED", "true").lower() in ("1", "true", "yes")

# 남용 방어(연결당). per-IP/전역 연결 cap은 앞단 프록시(ALB/nginx) 영역.
SESSION_INIT_TIMEOUT_S = float(env("SESSION_INIT_TIMEOUT_S", "10") or "10")  # 미인증 idle 끊기
MAX_TEXT_LEN = int(env("MAX_TEXT_LEN", "4000") or "4000")                    # text_turn 1건 상한
MAX_HISTORY_ITEMS = int(env("MAX_HISTORY_ITEMS", "200") or "200")           # session_init history 항목수
MAX_HISTORY_BYTES = int(env("MAX_HISTORY_BYTES", "200000") or "200000")     # history 총 바이트
MAX_TURNS_PER_MIN = int(env("MAX_TURNS_PER_MIN", "30") or "30")             # 연결당 분당 턴

# moly-llm 내부 호출 보호용 공유 시크릿(심층방어). 양 레포 동일값(Parameter Store).
# 비어있으면 미적용(로컬). moly-llm도 설정돼 있을 때만 강제 → 깔끔한 seam.
INTERNAL_SERVICE_TOKEN = env("INTERNAL_SERVICE_TOKEN")

# 에러 알림 — Slack Incoming Webhook URL. 비어있으면 알림 끔(no-op, 로컬 안전).
SLACK_WEBHOOK_URL = env("SLACK_WEBHOOK_URL")
