# WebSocket Interface (v2)

이 문서는 WebSocket 서버와 iOS 앱 클라이언트 사이의 인터페이스를 정의한다.

> v1(초안) 대비 변경 요약: **세션을 WebSocket 연결과 분리**(네트워크 끊김 ≠ 세션 종료,
> 재연결로 유지)했고, 그에 따라 `session_init`/`end_session`/`ready`/`session_committed`를
> 추가했다. 인증은 v1대로 `Authorization` 헤더를 쓴다. 인터럽트·히스토리·에러 처리 규칙을
> 명시했고, 최종 `transcript`에 `result`를 추가했다.

## 1. 연결과 세션

- 대화 엔드포인트: `WS /ws` (예: `wss://<host>/ws`)
- **세션 ≠ WebSocket 연결.** 세션은 *사용자의 의도*(대화 시작 ~ 사용자가 종료)이고,
  하나의 세션이 **여러 개의 WebSocket 연결에 걸칠 수 있다**(네트워크 끊김 → 재연결).
  - 세션 시작: WS 연결 + `session_init` 전송
  - 세션 종료: `end_session` 전송 (이때 서버가 장기 기억을 커밋한다)
  - **WS close는 세션 종료가 아니다.** 네트워크 끊김일 수 있으므로 **close에는 커밋하지 않는다.**
- 세션 ID는 별도로 전달하지 않는다. 신원은 토큰의 `user_id`로 식별한다.

**왜 이렇게 설계했나.** 모바일은 백그라운드 전환·셀 핸드오프·터널 등으로 연결이 자주 끊긴다.
끊겼다고 대화를 종료하면 사용자가 말하던 도중에 맥락이 사라진다. 따라서 끊김은 "이어가야 할
중단"으로 보고, 진짜 종료는 사용자가 명시적으로(`end_session`) 알린다.

### 인증

클라이언트는 WebSocket HTTP Upgrade 요청의 `Authorization` 헤더에 Supabase access token을
Bearer 형식으로 전달한다.

```http
GET /ws HTTP/1.1
Host: api.example.com
Upgrade: websocket
Connection: Upgrade
Authorization: Bearer <supabase_access_token>
```

- 서버는 Upgrade 수락 전에 토큰을 검증한다. 성공하면 `sub` claim을 `user_id`로 사용한다.
- 토큰이 없거나 형식이 잘못됐거나 검증에 실패하면 Upgrade하지 않고 HTTP **401**로 거부한다.
- 인증 실패는 WebSocket 연결 전 HTTP 단계에서 일어나므로 `error` 이벤트가 오지 않는다.
  클라이언트는 연결 실패를 인증 오류로 처리하고, 필요하면 Supabase 세션을 갱신해 새 토큰으로
  다시 연결한다.
- **재연결 때마다** 같은 방식으로 헤더에 (갱신된) 토큰을 실어 인증한다.

> 참고: 웹 브라우저 클라이언트는 WebSocket에 커스텀 헤더를 붙일 수 없어, 토큰을
> `session_init` 메시지로 전달하는 폴백을 사용한다. **iOS는 헤더 방식만 쓰면 된다.**

## 2. 전송 형식

하나의 WebSocket에서 두 종류의 프레임을 함께 사용한다.

| 프레임 | 클라이언트 → 서버 | 서버 → 클라이언트 |
|---|---|---|
| Text | UTF-8 JSON 제어 이벤트 | UTF-8 JSON 상태/텍스트 이벤트 |
| Binary | 마이크 PCM 오디오 | TTS PCM 오디오 |

바이너리 프레임에는 종류·샘플레이트·턴 ID 등의 헤더가 없다. 방향과 현재 상태로 의미를 구분한다.

### 오디오 규격

| 방향 | 규격 |
|---|---|
| 클라이언트 → 서버 | raw signed PCM16 little-endian, 16,000 Hz, mono |
| 서버 → 클라이언트 | raw signed PCM16 little-endian, 24,000 Hz, mono |

- WAV/MP3/AAC 컨테이너나 헤더를 붙이지 않는다.
- 사용자가 음성 입력을 시작하면 클라이언트는 `start` 전송과 마이크 캡처를 즉시 시작한다.
- `listening`을 받기 전의 PCM은 클라이언트의 FIFO 임시 버퍼에 저장하고 서버로 보내지 않는다.
- `listening`을 받으면 버퍼의 PCM부터 순서대로 전송한 뒤 실시간 PCM을 이어서 전송한다.
- 캡처 중 생성되는 모든 PCM은 하나의 직렬 전송 큐를 통과한다. 버퍼를 비우는 도중 새 PCM이
  먼저 전송되어 순서가 뒤섞이면 안 된다.
- 입력 종료 시 마이크 캡처를 먼저 중지하고, 전송 큐의 모든 PCM을 보낸 다음 `end`를 보낸다.
  (WebSocket의 프레임 순서 보장을 전제로 한다.)
- 출력 바이너리는 도착 순서대로 이어 붙여 재생한다.
- `turn_end`는 모든 TTS 바이트를 서버가 *전송*했다는 뜻이지, 기기에서 *재생*이 끝났다는
  뜻은 아니다.

## 3. 클라이언트 → 서버 이벤트

### `session_init`

WebSocket 연결 직후(재연결 포함) **가장 먼저** 보낸다. 이 세션의 대화 내역(history)을 서버에
시드한다. 서버는 `session_init`을 받기 전에는 다른 턴 이벤트를 처리하지 않는다.

```json
{
  "type": "session_init",
  "history": [
    {"role": "user", "content": "How are you?"},
    {"role": "assistant", "content": "I'm great, thanks!"}
  ]
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---:|---|
| `type` | `"session_init"` | O | 이벤트 종류 |
| `history` | array | O | 이 세션의 누적 대화. **첫 연결이면 빈 배열**, 재연결이면 그동안 쌓인 전체(또는 최근 N턴). |

- `role`은 `user`(사용자 발화) 또는 `assistant`(AI 응답)만 허용한다.
- 게이트웨이는 무상태다. 재연결 시 서버는 이 history로 대화 맥락을 **다시 시드**한다.
- 서버는 처리 완료 후 `ready`로 응답한다.

### `start`

음성 한 턴의 시작이다. 서버는 `idle`에서만 `start`를 수락하고 새 STT 연결을 준비한다.
이전 턴의 `turn_end`·`status/idle`을 받기 전에 새 `start`를 보내면 안 된다.

```json
{ "type": "start", "sampleRate": 16000 }
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---:|---|
| `type` | `"start"` | O | 이벤트 종류 |
| `sampleRate` | number | X | 진단용 실제 입력 샘플레이트. 서버 변환에는 쓰지 않는다. |

### Binary audio

`start`와 `end` 사이에 보내는 마이크 PCM 조각. JSON이 아닌 binary frame으로 전송한다.
서버는 현재 열린 STT가 없을 때 받은 바이너리를 조용히 버린다.

### `end`

현재 음성 입력을 확정한다.

```json
{ "type": "end" }
```

서버는 정상 인식 여부와 관계없이, 유효한 음성 입력의 `end`마다 최종 `transcript`를 **정확히
한 번** 보낸다(아래 `transcript`의 `result` 참고).

### `text_turn`

STT를 거치지 않고 텍스트 턴을 시작한다. 서버는 `idle`에서만 수락한다. 이전 턴의
`turn_end`·`status/idle`을 받기 전에 보내면 안 된다.

```json
{ "type": "text_turn", "text": "오늘 기분이 좋아." }
```

### `interrupt`

진행 중인 AI 응답(LLM 생성/TTS 재생)을 중단한다(barge-in).

```json
{ "type": "interrupt" }
```

- 서버는 진행 중인 턴을 취소하고 `status/idle`을 보낸다. 이 경우 **`turn_end`는 오지 않는다**(취소).
- 클라이언트는 즉시 TTS 재생을 멈춘다.
- **암묵적 인터럽트**: AI 응답 도중 새 `start`나 `text_turn`을 보내도 서버가 진행 턴을 자동
  취소하고 새 턴을 시작한다(별도 `interrupt` 없이도 barge-in 됨).

### `request_feedback`

현재까지 서버에 기록된 세션 대화의 교정을 요청한다.

```json
{ "type": "request_feedback" }
```

`feedback` 또는 `feedback_error` 중 하나가 온다. 새 요청이 오면 처리 중이던 이전 교정 요청은
취소된다. 요청과 응답을 잇는 ID가 없으므로 한 번에 하나만 요청한다.

### `end_session`

**사용자가 대화를 끝낼 때만** 보낸다. 서버가 이 세션의 대화를 장기 기억으로 커밋하는 유일한
트리거다.

```json
{ "type": "end_session" }
```

- 서버는 커밋 후 `session_committed`로 응답한다. 클라이언트는 응답을 받고 WS를 닫으면 된다.
- **네트워크 끊김(의도치 않은 close)에는 보내지 않는다.** 그 경우 서버는 커밋하지 않고,
  클라이언트는 재연결 후 이어간다.

## 4. 서버 → 클라이언트 이벤트

### `ready`

`session_init` 처리가 끝나 턴을 받을 준비가 됐음을 알린다.

```json
{ "type": "ready" }
```

WebSocket 연결 직후 서버가 초기 `idle`을 보내지는 않는다. 클라이언트는 `ready`를 받은 뒤
턴을 시작한다.

#### 선발화(서버가 먼저 말 거는 인사 턴)

서버 설정 `GREETING_ENABLED`(기본 켜짐)에서 **새 세션**(즉 `session_init.history`가 비어
있을 때)이면, 서버는 `ready` 직후 사용자 입력을 기다리지 않고 **Moly가 먼저 인사하는 턴**을
자동으로 연다. 클라이언트가 보내는 일반 턴과 **이벤트 형식이 완전히 동일**하다:

```
ready → status(thinking) → status(speaking) → reply_delta×N + binary audio → turn_end → status(idle)
```

- 클라이언트는 별도 처리 없이 평소 턴과 같게 렌더링/재생하면 된다(인사용 새 이벤트 타입 없음).
- `transcript`는 오지 않는다(사용자 발화가 아니므로). 인사 텍스트는 `reply_delta`로만 온다.
- **재연결**(누적 `history`가 실린 `session_init`)에서는 발동하지 않는다 — 중복 인사 방지.
- 클라이언트가 인사 도중 `start`/`text_turn`을 보내면(끼어들기) 서버는 진행 중인 인사 턴을
  취소한다(barge-in). 이 경우 `turn_end` 없이 `status/idle`만 올 수 있다(`interrupt`와 동일 규칙).

### `status`

```json
{ "type": "status", "state": "listening" }
```

| 상태 | 의미 |
|---|---|
| `idle` | 새 턴을 받을 수 있음 |
| `listening` | STT 준비됨 → 클라이언트가 버퍼의 PCM 전송 시작 가능 |
| `thinking` | 최종 사용자 입력을 얻었고 LLM 응답 생성 중 |
| `speaking` | 첫 TTS 문장 생성 시작 |

### `transcript`

중간 인식 결과:

```json
{ "type": "transcript", "text": "안녕하세", "final": false }
```

최종 인식(성공):

```json
{ "type": "transcript", "text": "안녕하세요", "final": true, "result": "recognized" }
```

최종(말하지 않음):

```json
{ "type": "transcript", "text": "", "final": true, "result": "no_speech" }
```

- `final: false`: 중간 결과. 누적 delta가 아니라 **현재 중간 문장 전체**로 취급해 화면을 교체한다.
- `final: true`: 해당 음성 입력의 종료 이벤트. `text`가 빌 수 있으며 `result`가 **반드시** 포함된다.
- 중간 이벤트에는 `result`를 넣지 않는다.
- 서버는 유효한 `end` 하나마다 최종 이벤트를 **정확히 한 번** 보낸다(빈 음성·오류 포함).

최종 `result`는 다음 네 값 중 하나다.

| `result` | `text` | 의미 | 이후 |
|---|---|---|---|
| `recognized` | 비어있지 않음 | 정상 인식 | `thinking`부터 AI 응답 진행 |
| `no_speech` | 빈 문자열 | 인식할 발화 없음 | AI 응답 없이 턴 종료 |
| `timeout` | 마지막 interim 또는 빈 문자열 | 제한 시간 내 최종화 실패 | AI 응답 없이 턴 종료 |
| `stt_error` | 마지막 interim 또는 빈 문자열 | STT 처리 오류 | AI 응답 없이 턴 종료 |

`no_speech`/`timeout`/`stt_error`에서도 **`final transcript → turn_end → status/idle` 순서를
보장**한다. `timeout`·`stt_error`의 마지막 interim 텍스트는 참고용이며 서버 대화 내역에는
추가하지 않는다.

### `reply_delta`

```json
{ "type": "reply_delta", "text": "반가워" }
```

LLM 응답의 증분 텍스트. 같은 턴의 `text`를 수신 순서대로 이어 붙인다. TTS 바이너리와 시간상
섞여 도착할 수 있다.

### Binary audio

TTS의 raw PCM16/24kHz/mono 조각. 같은 턴 안에서 도착 순서대로 gap 없이 예약 재생한다.

### `turn_end`

```json
{ "type": "turn_end" }
```

현재 입력 시도의 처리가 끝났음. 정상 응답에서는 텍스트·TTS 전송 완료를, `no_speech`/`timeout`/
`stt_error`에서는 AI 응답 없이 입력 처리가 끝났음을 의미한다. **모든 경우 `turn_end` 다음
`status/idle` 순서를 보장한다.** (단 `interrupt`로 취소된 경우는 `turn_end` 없이 `status/idle`만.)

### `session_committed`

`end_session`으로 요청한 장기 기억 커밋이 끝났음을 알린다.

```json
{ "type": "session_committed" }
```

### `error`

```json
{ "type": "error", "message": "오류 설명" }
```

메시지는 사용자 표시용 일반 문구다(내부 상세 비노출). 상황별 동작:

| 상황 | 동작 |
|---|---|
| `session_init` 전에 다른 이벤트 | `error` → **연결 유지**(해당 입력만 무시) |
| `session_init` 미수신(연결 후 일정 시간) | `error` → **연결 닫힘** |
| STT 연결 실패 | `error` → `status/idle` |
| LLM/TTS 처리 실패 | `error` → `status/idle` |
| 입력 텍스트 길이 초과 | `error` → **연결 유지** |
| 분당 턴 한도 초과 | `error` → **연결 유지** |
| 잘못된 JSON 프레임 | **무시하고 연결 유지** (한 프레임이 세션을 끊지 않음) |

### `feedback`

```json
{
  "type": "feedback",
  "data": {
    "has_corrections": true,
    "corrections": [
      {
        "type": "grammar",
        "original": "I go yesterday.",
        "corrected": "I went yesterday.",
        "explanation": "과거 시제에는 went를 사용합니다."
      }
    ]
  }
}
```

교정 `type`은 `grammar`·`vocabulary`·`naturalness` 중 하나다. 교정이 없으면:

```json
{ "type": "feedback", "data": { "has_corrections": false, "corrections": [] } }
```

### `feedback_error`

```json
{ "type": "feedback_error", "message": "오류 설명" }
```

## 5. 히스토리 관리

대화 내역(history)은 **클라이언트가 소유**하고, 매 (재)연결의 `session_init`으로 서버에 시드한다.
서버는 무상태이므로 이 history가 대화 맥락의 단일 출처다.

**클라이언트가 history에 추가하는 규칙**

- `{ "role": "user", ... }` — 음성은 `result: "recognized"`인 최종 `transcript`, 텍스트는
  `text_turn` 전송 시.
- `{ "role": "assistant", ... }` — `turn_end` 시, 그 턴의 누적 `reply_delta` 전체.
- `no_speech`/`timeout`/`stt_error` 턴은 **추가하지 않는다**(서버 대화 내역에도 안 들어간다).
- `interrupt`로 끊긴 턴은, 받은 `reply_delta`가 있으면 그것을 `assistant`로 추가하고,
  전혀 없으면 그 `user` 턴도 추가하지 않는다(연속 `user` 방지).

**서버**

- `session_init`의 history로 매번 대화 맥락을 교체하고, 연결 동안 턴마다 누적한다.
- `end_session` 때 누적된 대화를 장기 기억으로 커밋한다.
- `role`은 `user`/`assistant`만 허용하며, history 크기에 상한이 있다.

## 6. 대표 시퀀스

### 음성 턴 (정상 인식)

```text
Client                                  Server
  |--- WS connect (Authorization 헤더) ---->|  인증·accept
  |--- session_init(history) -------------->|
  |<-- ready --------------------------------|
  |    마이크 캡처 시작                          |
  |--- start ------------------------------->|
  |    PCM을 로컬 FIFO에 저장                    |
  |<-- status(listening) --------------------|
  |--- buffered PCM16/16k ------------------>|  오래된 PCM부터
  |<-- transcript(interim) ------------------|  0회 이상
  |--- live PCM16/16k ---------------------->|
  |    마이크 캡처 중지                          |
  |--- end --------------------------------->|
  |<-- transcript(final, result=recognized) -|
  |<-- status(thinking) ---------------------|
  |<-- reply_delta --------------------------|  1회 이상
  |<-- status(speaking) ---------------------|  TTS가 있을 때
  |<-- binary PCM16/24k ---------------------|  0회 이상
  |<-- turn_end -----------------------------|
  |<-- status(idle) -------------------------|
```

### 음성 턴 (빈 음성 / STT 실패)

```text
  |--- start ------------------------------->|
  |<-- status(listening) --------------------|
  |--- (buffered/live PCM, 없을 수도) ------->|
  |--- end --------------------------------->|
  |<-- transcript(final, result=no_speech|timeout|stt_error)
  |<-- turn_end -----------------------------|
  |<-- status(idle) -------------------------|
```

이 경로에서는 `thinking`·`reply_delta`·`speaking`·TTS 바이너리가 오지 않는다.

### 텍스트 턴

```text
  |--- text_turn(text) --------------------->|
  |<-- status(thinking) ---------------------|
  |<-- reply_delta / binary audio -----------|
  |<-- turn_end -----------------------------|
  |<-- status(idle) -------------------------|
```

텍스트 입력은 서버가 `transcript`로 되돌려 보내지 않는다. 사용자 말풍선은 클라이언트가 직접 표시한다.

### 네트워크 끊김 → 재연결 (세션 유지)

```text
  |   ...대화 중...                            |
  |--X  네트워크 끊김 → WS close              |   (서버 커밋 안 함)
  |--- WS reconnect (Authorization 헤더) ---->|   인증·accept
  |--- session_init(그동안 누적된 history) -->|
  |<-- ready --------------------------------|
  |   ...같은 세션 이어서 대화...               |
```

### 세션 종료와 교정

```text
  |--- request_feedback -------------------->|
  |<-- feedback | feedback_error ------------|
  |--- end_session ------------------------->|   장기 기억 커밋
  |<-- session_committed --------------------|
  |--- WS close ---------------------------->|
```

교정이 필요 없으면 `request_feedback` 없이 바로 `end_session` → close 해도 된다.

## 부록 — 미해결 합의 사항

- **앱 강제종료/백그라운드로 `end_session` 없이 닫히는 경우**: 그 세션 기억은 유실된다
  (네트워크 끊김과 구분 불가하므로 close로 커밋할 수 없다). 현재는 유실 허용. 필요하면
  서버측 idle 타임아웃 안전망(끊긴 뒤 일정 시간 무활동 시 자동 커밋)을 추가할 수 있다. → 결정 필요.
