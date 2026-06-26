// Moly 데모 클라이언트 — 세션 생애주기(시작→대화→끊기→교정→복귀) + 채팅/음성.
// 새 세션은 이전 대화 안 보여줌(로그 비움). 장기기억은 서버가 연결 시 로드(화면엔 안 보임).

const $ = (id) => document.getElementById(id);
let ws, inCtx, micNode, micSource, micStream, outCtx;
let nextTime = 0, sources = [];
let listening = false, replyBuf = "", liveYou = null, mode = "chat", sending = false;
let tEnd = 0, tStt = 0, tReply = 0, tAudio = 0;
let pendingListen = null;
// 세션 상태머신: idle(랜딩) → active(대화) → ending(교정 대기) → ended(교정 표시·랜딩)
let state = "idle", pendingFeedback = null, spoke = false;

function setStatus(s) { $("status").textContent = s; $("status").className = s; }

function bubble(cls, text) {
  const d = document.createElement("div");
  d.className = "bubble " + cls;
  d.textContent = text;
  $("log").appendChild(d);
  $("log").scrollTop = $("log").scrollHeight;
  return d;
}

// ── WebSocket (싱글톤 — 새 WS 만들 땐 옛 핸들러 제거해 좀비 콜백 차단) ──
let connectPromise = null;
function connect() {
  if (ws && ws.readyState === 1) return Promise.resolve();
  if (connectPromise) return connectPromise;
  if (ws) { ws.onclose = ws.onmessage = ws.onerror = ws.onopen = null; }
  connectPromise = new Promise((resolve, reject) => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.binaryType = "arraybuffer";
    ws.onmessage = onMessage;
    ws.onopen = () => resolve();
    ws.onerror = () => { connectPromise = null; reject(new Error("서버 연결 실패")); };
    ws.onclose = onWsClose;
  });
  return connectPromise;
}

function onWsClose() {
  connectPromise = null;
  setStatus("disconnected");
  if (state === "active") {                 // 예상치 못한 끊김(서버 재시작·네트워크) → 랜딩 복귀
    state = "idle";
    stopPlayback(); if (listening) abortListening(); finalizeLive();
    showLanding("몰리와 대화하기");
    $("feedback").innerHTML = '<div class="fb-empty">연결이 끊겼어요. 다시 시작해 주세요.</div>';
  }
  // ending/ended(의도적 종료)는 무시 — 교정 패널 유지
}

// 오디오 재생 컨텍스트 확보(브라우저 정책상 user gesture 안에서 호출돼야 함).
async function ensureAudio() {
  if (!outCtx) outCtx = new AudioContext({ sampleRate: 24000 });
  await outCtx.resume();
}

function onMessage(ev) {
  if (typeof ev.data !== "string") {
    if (!tAudio) tAudio = performance.now();
    playPCM(ev.data);
    return;
  }
  const m = JSON.parse(ev.data);
  if (m.type === "transcript") {
    if (!liveYou) liveYou = bubble("you live", "");
    liveYou.textContent = m.text;
    if (m.final) { spoke = true; if (!tStt) tStt = performance.now(); liveYou.classList.remove("live"); liveYou = null; }
  } else if (m.type === "reply_delta") {
    if (!tReply) tReply = performance.now();
    replyBuf += m.text;
    if (!window._molly) window._molly = bubble("molly live", "");
    window._molly.textContent = replyBuf;
  } else if (m.type === "turn_end") {
    if (window._molly) window._molly.classList.remove("live");
    window._molly = null; replyBuf = "";
    showLatency();
  } else if (m.type === "status") {
    setStatus(m.state);
    if (m.state === "listening" && pendingListen) { pendingListen.resolve(); pendingListen = null; }
  } else if (m.type === "feedback") {
    if (pendingFeedback) { pendingFeedback.resolve(m.data); pendingFeedback = null; }
  } else if (m.type === "feedback_error") {
    if (pendingFeedback) { pendingFeedback.resolve({ __error: true }); pendingFeedback = null; }
  } else if (m.type === "error") {
    if (pendingListen) { pendingListen.reject(new Error(m.message)); pendingListen = null; }
    bubble("molly", "⚠️ " + m.message);
  }
}

// ── 세션 생애주기 ──
function showSession() {
  $("start").classList.add("hidden");
  $("session").classList.remove("hidden");
  $("end").disabled = false; $("end").textContent = "연결 끊기";
  $("msg").focus();
}
function showLanding(label) {
  $("session").classList.add("hidden");
  $("start").classList.remove("hidden");
  $("start").disabled = false; $("start").textContent = label;
}
function finalizeLive() {
  if (window._molly) { window._molly.classList.remove("live"); window._molly = null; }
  if (liveYou) { liveYou.classList.remove("live"); liveYou = null; }
  replyBuf = "";
}
function resetSessionState() {
  stopPlayback();
  if (listening) abortListening();
  finalizeLive();
  if (pendingListen) { try { pendingListen.reject(new Error("reset")); } catch (e) {} pendingListen = null; }
  listening = false; sending = false; pendingFeedback = null; spoke = false;
  tEnd = tStt = tReply = tAudio = 0; nextTime = 0;
  $("log").innerHTML = ""; $("feedback").innerHTML = "";
  setMode("chat");
}

async function startSession() {
  if (state === "active" || state === "ending") return;
  $("start").disabled = true; $("start").textContent = "연결 중…";
  try {
    await ensureAudio();               // 이 클릭(제스처) 안에서 — 이후 TTS 재생 가능
    resetSessionState();               // 이전 세션 로그·교정·상태 초기화
    await connect();                   // 서버: 장기기억만 로드(load_memory)
    if (!ws || ws.readyState !== 1) throw new Error("연결 실패");
    state = "active";
    showSession();
  } catch (e) {
    state = "idle";
    showLanding("몰리와 대화하기");
    $("feedback").innerHTML = '<div class="fb-empty">연결에 실패했어요. 다시 시도해 주세요.</div>';
  }
}

function awaitFeedback(timeoutMs) {
  return new Promise((resolve, reject) => {
    if (!ws || ws.readyState !== 1) { reject(new Error("연결 없음")); return; }
    pendingFeedback = { resolve };
    ws.send(JSON.stringify({ type: "request_feedback" }));
    setTimeout(() => { if (pendingFeedback) { pendingFeedback = null; reject(new Error("시간 초과")); } }, timeoutMs);
  });
}

async function endSession() {
  if (state !== "active") return;        // 중복 클릭·잘못된 상태 차단
  state = "ending";
  $("end").disabled = true; $("end").textContent = "마무리 중…";
  // 진행 중이던 턴 중단(서버 turn 취소) + 클라 재생·마이크·라이브버블 정리.
  try { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "interrupt" })); } catch (e) {}
  stopPlayback(); abortListening(); finalizeLive();

  if (spoke) {                            // 뭔가 말했을 때만 교정
    $("feedback").innerHTML = '<div class="fb-empty">교정 중…</div>';
    try {
      const data = await awaitFeedback(35000);   // 서버 httpx 30s보다 길게
      if (data && data.__error) $("feedback").innerHTML = '<div class="fb-empty">교정을 불러오지 못했어요.</div>';
      else renderFeedback(data);
    } catch (e) {
      $("feedback").innerHTML = '<div class="fb-empty">교정 시간이 초과됐어요.</div>';
    }
  } else {
    $("feedback").innerHTML = "";
  }

  try { if (ws) ws.close(); } catch (e) {}   // 서버 finally가 메모리 커밋
  state = "ended";
  showLanding("다시 대화하기");
}

// ── 교정 렌더 ──
function renderFeedback(data) {
  const el = $("feedback");
  el.innerHTML = "";
  if (!data || !data.has_corrections || !(data.corrections || []).length) {
    el.innerHTML = '<div class="fb-empty">이번 대화는 교정할 부분이 없었어요.</div>';
    return;
  }
  const title = document.createElement("div");
  title.className = "fb-title";
  title.textContent = "교정 (" + data.corrections.length + ")";
  el.appendChild(title);
  const TYPE = { grammar: "문법", vocabulary: "단어", naturalness: "자연스러움" };
  for (const c of data.corrections) {
    const card = document.createElement("div");
    card.className = "corr";
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = TYPE[c.type] || c.type;
    const line = document.createElement("div");
    const orig = document.createElement("span"); orig.className = "orig"; orig.textContent = c.original;
    const arrow = document.createElement("span"); arrow.className = "arrow"; arrow.textContent = "→";
    const fix = document.createElement("span"); fix.className = "fix"; fix.textContent = c.corrected;
    line.append(orig, arrow, fix);
    const why = document.createElement("div"); why.className = "why"; why.textContent = c.explanation;
    card.append(badge, line, why);
    el.appendChild(card);
  }
}

function showLatency() {
  if (!tEnd || !tAudio) return;
  const total = Math.round(tAudio - tEnd);
  const stt = tStt ? Math.round(tStt - tEnd) : null;
  const llm = (tStt && tReply) ? Math.round(tReply - tStt) : null;
  const parts = [`체감 ${total}ms`];
  if (stt != null) parts.push(`STT ${stt}`);
  if (llm != null) parts.push(`LLM ${llm}`);
  const d = document.createElement("div");
  d.className = "latency";
  d.textContent = "⏱ " + parts.join(" · ");
  $("log").appendChild(d);
  $("log").scrollTop = $("log").scrollHeight;
}

// ── 재생 (PCM 24k) ──
function playPCM(arrayBuffer) {
  if (!outCtx || state === "ending" || state === "ended") return;  // 종료 중엔 잔여 오디오 무시
  const i16 = new Int16Array(arrayBuffer);
  const f32 = new Float32Array(i16.length);
  for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768;
  const buf = outCtx.createBuffer(1, f32.length, 24000);
  buf.copyToChannel(f32, 0);
  const src = outCtx.createBufferSource();
  src.buffer = buf; src.connect(outCtx.destination);
  const t = Math.max(outCtx.currentTime + 0.02, nextTime);
  src.start(t); nextTime = t + buf.duration;
  sources.push(src);
  src.onended = () => { sources = sources.filter((s) => s !== src); };
}
function stopPlayback() {
  sources.forEach((s) => { try { s.stop(); } catch (e) {} });
  sources = []; nextTime = 0;
}

// ── 채팅 전송 ──
async function sendChat() {
  if (state !== "active" || sending) return;
  const text = $("msg").value.trim();
  if (!text) return;
  sending = true;
  try {
    await connect();
    if (!ws || ws.readyState !== 1) throw new Error("연결 준비 중이에요. 잠시 후 다시 시도하세요.");
    stopPlayback();                           // 이전 응답 말하던 중이면 끊고(barge-in)
    ws.send(JSON.stringify({ type: "text_turn", text }));
    bubble("you", text); spoke = true;
    $("msg").value = "";
  } catch (e) {
    bubble("molly", "⚠️ " + (e.message || e));
  } finally {
    sending = false;
  }
}

// ── 마이크 (음성) ──
function waitForListening() {
  if (pendingListen) pendingListen.reject(new Error("start superseded"));
  return new Promise((resolve, reject) => {
    const token = { resolve, reject };
    pendingListen = token;
    setTimeout(() => {
      if (pendingListen !== token) return;
      pendingListen = null; reject(new Error("STT listening timeout"));
    }, 8000);
  });
}
async function prepareMic() {
  inCtx = new AudioContext({ sampleRate: 16000 });
  await inCtx.audioWorklet.addModule("mic-worklet.js");
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });
  micSource = inCtx.createMediaStreamSource(micStream);
  micNode = new AudioWorkletNode(inCtx, "mic-processor");
  micNode.port.onmessage = (e) => { if (ws && ws.readyState === 1) ws.send(e.data); };
}
function startMic() { if (micSource && micNode) micSource.connect(micNode); }
function stopMic() {
  if (micStream) micStream.getTracks().forEach((t) => t.stop());
  if (micSource) micSource.disconnect();
  if (micNode) micNode.disconnect();
  if (inCtx) inCtx.close();
  micStream = micNode = micSource = inCtx = null;
}

async function toggleTalk() {
  if (state !== "active") return;
  try {
    await connect();
    await ensureAudio();
    if (!ws || ws.readyState !== 1) throw new Error("서버 연결 준비 중");
    if (!listening) {
      stopPlayback();
      await prepareMic();
      ws.send(JSON.stringify({ type: "start", sampleRate: inCtx.sampleRate }));
      await waitForListening();
      startMic();
      listening = true;
      $("talk").textContent = "■ 종료 (전송)";
      $("talk").classList.add("on");
    } else {
      tEnd = performance.now(); tStt = tReply = tAudio = 0;
      ws.send(JSON.stringify({ type: "end" })); spoke = true;
      stopMic();
      listening = false;
      $("talk").textContent = "🎤 말하기 시작";
      $("talk").classList.remove("on");
    }
  } catch (e) {
    bubble("molly", "⚠️ " + (e.message || e));
    stopMic(); listening = false; $("talk").classList.remove("on");
    $("talk").textContent = "🎤 말하기 시작";
  }
}

// 음성 캡처 중 중단(전사 버림). 끊기/모드전환 시 사용.
function abortListening() {
  if (!listening) return;
  try { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "interrupt" })); } catch (e) {}
  stopMic(); listening = false;
  $("talk").textContent = "🎤 말하기 시작";
  $("talk").classList.remove("on");
}

// ── 모드 토글 ──
function setMode(m) {
  if (mode === "voice" && m !== "voice") abortListening();
  mode = m;
  $("chatbar").classList.toggle("hidden", m !== "chat");
  $("voicebar").classList.toggle("hidden", m !== "voice");
  document.querySelectorAll("#mode button").forEach((b) =>
    b.classList.toggle("active", b.dataset.mode === m));
  if (m === "chat" && state === "active") $("msg").focus();
}

// ── 바인딩 ──
$("start").onclick = startSession;
$("end").onclick = endSession;
document.querySelectorAll("#mode button").forEach((b) =>
  (b.onclick = () => setMode(b.dataset.mode)));
$("send").onclick = sendChat;
$("msg").addEventListener("keydown", (e) => {
  // 한글 등 IME 조합 중 Enter는 조합 확정용 → 전송 금지(조합 끝난 Enter만 전송).
  if (e.key === "Enter" && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); sendChat(); }
});
$("talk").onclick = toggleTalk;
