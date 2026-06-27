# moly-voice — 실시간 음성/채팅 게이트웨이

Moly 대화의 **실시간 레이어**. 앱과 WebSocket으로 오디오·텍스트를 주고받으며 한 턴을
오케스트레이션한다(STT → LLM → TTS). **무상태** 컨테이너.

> 프로덕션 3레포 중 **③ gateway**. ① server(인증·세션) · ② moly-llm(응답·장기기억) · ③ gateway(이 레포).

## 한 턴

```
앱 ─WS─▶ STT(provider) ─▶ ② moly-llm(/chat SSE) ─▶ 문장분할 ─▶ TTS(ElevenLabs) ─WS─▶ 앱
```

- 음성: 마이크 PCM16/16k → STT → 텍스트. 채팅: `text_turn` 텍스트 직접.
- 그 뒤(LLM → 문장분할 → TTS)는 음성·채팅이 100% 공유. 첫 문장 끝나는 즉시 TTS 시작(지연↓).
- barge-in: 응답 도중 `start`/`text_turn`/`interrupt` → 진행 턴 취소.

## 세션 모델 (중요)

**세션 ≠ WebSocket 연결.** 세션은 사용자 의도(대화 시작~종료)이고, WS 연결은 그 안에서
여러 번 맺어질 수 있다(네트워크 끊김 → 재연결).

- **history 소유 = 클라(앱)**. 연결/재연결마다 `session_init`으로 게이트웨이에 시드 → 무상태.
- **mem0 커밋 = 명시적 `end_session`일 때만.** WS 끊김(네트워크)으론 커밋하지 않음(끊김 ≠ 종료).
- 전체 WS 계약: [`docs/websocket-interface.md`](docs/websocket-interface.md).

## 인증

- WS `session_init`의 Supabase access token을 `/auth/v1/user`(remote getUser, server
  `require-user.ts`와 동일 경로)로 검증 → `user_id` 도출. (네이티브는 `Authorization` 헤더 — 계약 문서 참고)
- `REQUIRE_AUTH=true`(프로덕션)면 유효 토큰 필수, `false`(로컬/데모)면 `DEMO_USER_ID` 폴백.

## STT/TTS provider

- **STT**: `STT_PROVIDER`로 선택 — `deepgram`(Nova-3, 기본) | `elevenlabs`(Scribe v2 Realtime).
  공통 인터페이스(`app/stt/base.py STTStream`) 뒤로 추상화 → env 한 줄로 A/B 전환.
- **TTS**: ElevenLabs Flash v2.5 (Jessica). 출력 PCM16/24k.

## 보안/운영

- 인증 게이트(`session_init` 전 턴 거절) + 미인증 idle 타임아웃
- 남용 방어(연결당): 메시지 길이·history 크기·분당 턴 상한
- moly-llm 내부 호출에 공유 시크릿 헤더(`X-Internal-Token`)
- Slack 에러 알림(`alerts.py`, fail-safe)

## 구조

```
app/
├── main.py              # FastAPI: /health, /ws (세션 생애주기·인증·턴 라우팅)
├── config.py            # env
├── auth.py              # Supabase 토큰 검증
├── alerts.py            # Slack 에러 알림 + 내부 호출 헤더
├── memory.py            # ② /memory/load·commit 호출(장기기억)
├── feedback.py          # ② /feedback 호출(교정)
├── llm.py               # ② /chat SSE 스트리밍
├── gateway/orchestrator.py   # run_turn: LLM 스트림 → 문장분할 → TTS
├── stt/                 # base(STTStream) · factory · deepgram · elevenlabs_scribe
├── tts/elevenlabs.py
└── shared/splitter.py   # 문장 분할
web/                     # 데모 클라이언트(push-to-talk + 채팅)
docs/                    # websocket-interface.md (WS 계약)
tests/                   # 프로토콜·STT 하버스(아래)
```

## 로컬 실행 (데모 웹)

`② moly-llm`이 떠 있어야 함(`/chat`·`/memory`·`/feedback`).

```bash
cp .env.example .env     # 키 채우기 (DEEPGRAM/ELEVENLABS/SUPABASE 등, LLM_URL=moly-llm 루트)
pip install -r requirements.txt
uvicorn app.main:app --port 8001
```

→ Chrome에서 `http://localhost:8001` (localhost는 보안 컨텍스트라 마이크 허용). 🎤로 말하고 다시
눌러 전송, 또는 채팅 입력. 로컬은 `REQUIRE_AUTH=false`라 토큰 없이 DEMO로 동작.

## 테스트

```bash
.venv/bin/python tests/test_session_protocol.py   # 세션·인증·재연결·하드닝 (27)
.venv/bin/python tests/test_stt_factory.py        # STT 추상화·팩토리 (11)
.venv/bin/python tests/test_stt_elevenlabs.py     # Scribe 어댑터 프로토콜 (10)
.venv/bin/python tests/test_stt_pump.py           # _pump_stt interim 누적 (3)
```

## env (주요)

| 키 | 설명 |
|---|---|
| `DEEPGRAM_API_KEY` | STT(Deepgram) |
| `ELEVENLABS_API_KEY` / `ELEVENLABS_VOICE_ID` | TTS(+ Scribe STT 인증 재사용) |
| `STT_PROVIDER` | `deepgram`(기본) \| `elevenlabs` \| `soniox` |
| `STT_LANGUAGE` | 인식 언어(기본 `en`) |
| `LLM_URL` | ② moly-llm 루트(`.../chat`) — memory/feedback URL 파생 |
| `SUPABASE_URL` / `SUPABASE_ANON_KEY` | WS 토큰 검증 |
| `REQUIRE_AUTH` | `true`=토큰 필수(프로덕션), `false`=DEMO 폴백(로컬) |
| `INTERNAL_SERVICE_TOKEN` | moly-llm 내부 호출 보호(양 레포 동일값) |
| `SLACK_WEBHOOK_URL` | 에러 알림(없으면 off) |
| `MAX_TEXT_LEN` / `MAX_HISTORY_ITEMS` / `MAX_HISTORY_BYTES` / `MAX_TURNS_PER_MIN` / `SESSION_INIT_TIMEOUT_S` | 남용 방어 노브 |

전체는 [`.env.example`](.env.example).

## 배포

`main` merge → GitHub Actions → ECR → EC2(SSM `moly-infra/deploy.sh`). 시크릿은 Parameter
Store `/moly/prod/*` → deploy.sh가 컨테이너 env로 매핑. 확인: `voice.moly.asia`.
