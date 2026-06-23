# 실시간 음성 데모 (웹, push-to-talk) — 계획

웹에서 마이크로 Molly와 실시간 대화하는 데모. **버튼으로 발화 시작/종료를 유저가 명시**(push-to-talk)
→ 턴 경계가 확실해 VAD/자동종료 불필요.

## 범위
- 웹 ↔ moly-voice(WebSocket) ↔ STT(Deepgram) / LLM(/chat) / TTS(ElevenLabs)
- 데모 제외: 인증·Supabase 영속(세션은 게이트웨이 메모리)
- LLM은 기존 `/chat` 계약 호출(`LLM_URL`, 기본 기존 Node 서버)

## 상태 머신 (버튼 토글)
```
IDLE ─(버튼:시작)→ LISTENING ─(버튼:종료)→ THINKING ─(첫문장)→ SPEAKING ─(끝)→ IDLE
SPEAKING ─(버튼:끼어들기)→ LISTENING   # barge-in: TTS 중단
```

## WS 프로토콜 (웹 ↔ 게이트웨이)
| 방향 | 메시지 |
|---|---|
| 웹→ | `{type:start}` → 바이너리 PCM16 16k 프레임 → `{type:end}` / `{type:interrupt}` |
| →웹 | `{type:transcript,text,final}` · `{type:reply_delta,text}` · 바이너리 PCM 24k · `{type:status,state}` · `{type:turn_end}` · `{type:error}` |

## 구현 항목
### 게이트웨이 (moly-voice)
- `/ws` 핸들러 — 연결당 세션(messages[], user_id, state)
- start→Deepgram STT WS 열기 / 오디오 프레임 즉시 포워딩 / end→finalize→최종 transcript
- transcript→messages→`/chat`(SSE)→문장분할→ElevenLabs TTS→오디오 포워딩(+reply_delta)
- turn_end→messages에 assistant 추가 / interrupt→LLM·TTS 중단
- async 포팅: harness의 deepgram/elevenlabs(sync, 파일기반) → async + live 프레임
- `/` 정적 서빙(데모 페이지)

### 웹 클라이언트
- 마이크: getUserMedia→AudioContext→AudioWorklet(48k→16k, Float32→Int16)→WS
- WS 송수신, 토글 버튼(상태 반영), transcript/reply 표시, 상태 표시
- 재생: PCM 24k 청크 → AudioBuffer 연속 스케줄링(갭 없이)
- barge-in: SPEAKING 중 버튼 → 재생 중단 + interrupt

## 오디오 포맷
| 구간 | 포맷 | 주의 |
|---|---|---|
| 마이크→GW | PCM16 16k mono | 브라우저 48k → 다운샘플 |
| GW→Deepgram | linear16 16k | end에 Finalize |
| ElevenLabs→웹 | PCM 24k | 입력과 레이트 다름 |
| 웹 재생 | 24k | 연속 스케줄링 |

## Phase
1. WS+마이크 에코(오디오 흐름 확인)
2. STT(live→Deepgram→transcript 표시)
3. LLM(transcript→/chat→reply 표시)
4. TTS+재생(단일 턴 E2E)
5. 멀티턴(세션 messages[])
6. 폴리시(상태 UI·barge-in·지연)

## 재사용
`moly-pipeline-test/voice-harness/harness/{stt/deepgram_stt,tts/elevenlabs_tts,sentence_splitter}` 의
프로토콜/파라미터를 가져와 **async + live 프레임**으로 개조.

## 검증
브라우저 없이 **시뮬 클라이언트**(wav PCM 프레임을 WS로 주입)로 게이트웨이 STT→LLM→TTS 검증.
