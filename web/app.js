// Moly 데모 클라이언트 — 채팅(text_turn) + 음성(push-to-talk), 같은 WS·대화로그 공유.
// 마이크 PCM16 16k → /ws → STT/LLM/TTS → PCM24k 재생. 채팅도 TTS로 음성 응답.

const $ = (id) => document.getElementById(id);
let ws, inCtx, micNode, micSource, micStream, outCtx;
let nextTime = 0, sources = [];
let listening = false, replyBuf = "", liveYou = null, muted = false, mode = "chat";
let tEnd = 0, tStt = 0, tReply = 0, tAudio = 0;
let pendingListen = null;

function setStatus(s) { $("status").textContent = s; $("status").className = s; }

function bubble(cls, text) {
  const d = document.createElement("div");
  d.className = "bubble " + cls;
  d.textContent = text;
  $("log").appendChild(d);
  $("log").scrollTop = $("log").scrollHeight;
  return d;
}

// ── WebSocket ──
async function connect() {
  if (ws && ws.readyState === 1) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.binaryType = "arraybuffer";
  ws.onmessage = onMessage;
  ws.onclose = () => setStatus("disconnected");
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
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
    if (m.final) { if (!tStt) tStt = performance.now(); liveYou.classList.remove("live"); liveYou = null; }
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
  } else if (m.type === "error") {
    if (pendingListen) { pendingListen.reject(new Error(m.message)); pendingListen = null; }
    bubble("molly", "⚠️ " + m.message);
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

// ── 재생 (PCM 24k) ── 음소거 시 스킵.
function playPCM(arrayBuffer) {
  if (!outCtx || muted) return;
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
  const text = $("msg").value.trim();
  if (!text) return;
  try {
    await connect();
    await ensureAudio();          // 클릭=user gesture → TTS 재생 가능
    stopPlayback();               // 이전 응답 말하던 중이면 끊고(barge-in)
    bubble("you", text);          // 내 메시지는 클라가 즉시 렌더(서버 transcript 불필요)
    ws.send(JSON.stringify({ type: "text_turn", text }));
    $("msg").value = "";
  } catch (e) {
    bubble("molly", "⚠️ " + (e.message || e));
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
  try {
    await connect();
    await ensureAudio();
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
      ws.send(JSON.stringify({ type: "end" }));
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

// 음성 캡처 중 다른 모드로 전환 시 깔끔히 중단(전사 버림).
function abortListening() {
  if (!listening) return;
  try { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "interrupt" })); } catch (e) {}
  stopMic(); listening = false;
  $("talk").textContent = "🎤 말하기 시작";
  $("talk").classList.remove("on");
}

// ── 모드 토글 ──
function setMode(m) {
  if (m === mode) return;
  if (mode === "voice") abortListening();
  mode = m;
  $("chatbar").classList.toggle("hidden", m !== "chat");
  $("voicebar").classList.toggle("hidden", m !== "voice");
  document.querySelectorAll("#mode button").forEach((b) =>
    b.classList.toggle("active", b.dataset.mode === m));
  if (m === "chat") $("msg").focus();
}

function setMuted(v) {
  muted = v;
  if (muted) stopPlayback();
  const icon = muted ? "🔇" : "🔊";
  $("mute").textContent = icon; $("mute2").textContent = icon;
}

// ── 바인딩 ──
document.querySelectorAll("#mode button").forEach((b) =>
  (b.onclick = () => setMode(b.dataset.mode)));
$("send").onclick = sendChat;
$("msg").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); sendChat(); } });
$("talk").onclick = toggleTalk;
$("mute").onclick = () => setMuted(!muted);
$("mute2").onclick = () => setMuted(!muted);
