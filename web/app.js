// Molly 실시간 음성 데모 클라이언트 (push-to-talk).
// 마이크 PCM16 16k → /ws → STT/LLM/TTS → PCM24k 재생.

const $ = (id) => document.getElementById(id);
let ws, inCtx, micNode, micStream, outCtx;
let nextTime = 0, sources = [];
let listening = false, replyBuf = "", liveYou = null;

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

function onMessage(ev) {
  if (typeof ev.data !== "string") { playPCM(ev.data); return; }
  const m = JSON.parse(ev.data);
  if (m.type === "transcript") {
    if (!liveYou) liveYou = bubble("you live", "");
    liveYou.textContent = m.text;
    if (m.final) { liveYou.classList.remove("live"); liveYou = null; }
  } else if (m.type === "reply_delta") {
    replyBuf += m.text;
    if (!window._molly) window._molly = bubble("molly live", "");
    window._molly.textContent = replyBuf;
  } else if (m.type === "turn_end") {
    if (window._molly) window._molly.classList.remove("live");
    window._molly = null; replyBuf = "";
  } else if (m.type === "status") {
    setStatus(m.state);
  } else if (m.type === "error") {
    bubble("molly", "⚠️ " + m.message);
  }
}

// ── 재생 (PCM 24k 연속 스케줄링) ──
function playPCM(arrayBuffer) {
  if (!outCtx) return;
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

// ── 마이크 ──
async function startMic() {
  inCtx = new AudioContext({ sampleRate: 16000 });
  await inCtx.audioWorklet.addModule("mic-worklet.js");
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });
  const srcNode = inCtx.createMediaStreamSource(micStream);
  micNode = new AudioWorkletNode(inCtx, "mic-processor");
  micNode.port.onmessage = (e) => { if (ws && ws.readyState === 1) ws.send(e.data); };
  srcNode.connect(micNode);
}
function stopMic() {
  if (micStream) micStream.getTracks().forEach((t) => t.stop());
  if (micNode) micNode.disconnect();
  if (inCtx) inCtx.close();
  micStream = micNode = inCtx = null;
}

// ── 버튼(토글) ──
$("talk").onclick = async () => {
  try {
    await connect();
    if (!outCtx) outCtx = new AudioContext({ sampleRate: 24000 });
    await outCtx.resume();

    if (!listening) {
      stopPlayback();                          // 말하던 중이면 끊고(barge-in 로컬)
      await startMic();
      ws.send(JSON.stringify({ type: "start" })); // 게이트웨이도 진행 턴 cancel
      listening = true;
      $("talk").textContent = "■ 종료 (전송)";
      $("talk").classList.add("on");
    } else {
      ws.send(JSON.stringify({ type: "end" }));
      stopMic();
      listening = false;
      $("talk").textContent = "🎤 말하기 시작";
      $("talk").classList.remove("on");
    }
  } catch (e) {
    bubble("molly", "⚠️ " + (e.message || e));
    listening = false; $("talk").classList.remove("on");
  }
};
