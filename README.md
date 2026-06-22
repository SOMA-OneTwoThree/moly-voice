# moly-voice — 실시간 음성 게이트웨이

Molly 음성 대화의 **실시간 레이어**. 앱과 WebSocket으로 오디오를 주고받으며 한 턴을 오케스트레이션한다.

> 프로덕션 3레포 중 **③ STT-TTS**. 전체 구조는 `moly-pipeline-test/docs/production-architecture.md` 참고.

## 역할 (실시간 오케스트레이터)
```
앱 오디오 ─WS─▶ STT(Deepgram) ─▶ ② moly-llm(SSE) ─▶ TTS(ElevenLabs) ─WS─▶ 앱 오디오
```
- 앱과의 **WebSocket 보유**(영속) — 서버가 서버리스라 실시간은 여기서 담당
- **세션 대화(messages[])** 메모리 보관 → ② LLM 호출 시 전달, 턴마다 ① server에 async 영속
- **턴테이킹/barge-in**(사용자가 끼어들면 진행 중 TTS 중단)
- 문장 파이프라이닝(첫 문장 끝나는 즉시 TTS 시작 → 지연↓)

## 스택
- **Python FastAPI** (컨테이너, 영속 WebSocket — 서버리스 불가, 콜드스타트 회피)
- STT: **Deepgram Nova-3** (스트리밍 WS) — provider 추상화(assemblyai 대체)
- TTS: **ElevenLabs Flash v2.5 (Jessica)** (스트리밍 WS) — provider 추상화(openai 대체)

## 구조
```
app/
├── main.py            # FastAPI: /health, /ws
├── gateway/           # session(상태), orchestrator(턴 루프), turn_taking(barge-in)
├── stt/providers/     # deepgram (+ assemblyai)
├── tts/providers/     # elevenlabs (+ openai)
└── shared/            # PCM16 포맷, VAD, 오디오 유틸
```

## 인터페이스 (앱 ↔ /ws)
| 방향 | 메시지 |
|---|---|
| 앱 → | 오디오 청크(PCM 바이너리), `{type:"start"}` / `{type:"stop"}` |
| → 앱 | TTS 오디오(바이너리), `{type:"transcript",text,final}`, `{type:"turn_end"}`, `{type:"barge_in"}` |

## 로컬 실행
```bash
cp .env.example .env    # DEEPGRAM/ELEVENLABS 키, LLM_URL=localhost:8000
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
```

## env
| 키 | 설명 |
|---|---|
| `DEEPGRAM_API_KEY` | STT |
| `ELEVENLABS_API_KEY` / `ELEVENLABS_VOICE_ID` | TTS / Jessica(`cgSgspJ2msm6clMCkdW9`) |
| `LLM_URL` | ② moly-llm 내부 URL |
| `SERVER_URL` | ① moly-server (세션·영속) |

## 배포
Dockerfile(ffmpeg 포함) → **Railway/Fly (항상 켜짐)**. ② moly-llm 과 같은 플랫폼에 co-locate.

## 재사용 메모
`moly-pipeline-test/voice-harness`의 `harness/stt`·`harness/tts`·`harness/pipeline.py`(문장 파이프라이닝)·
`realtime_s2s`의 오디오 유틸을 옮겨온다.
