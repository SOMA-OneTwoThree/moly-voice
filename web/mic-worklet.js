// 마이크 PCM 워크릿: 16kHz Float32(컨텍스트 레이트) → Int16 변환 후 메인 스레드로 전송.
// (AudioContext를 sampleRate:16000 으로 만들면 브라우저가 마이크를 16k로 리샘플해줌)
class MicProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch && ch.length) {
      const pcm = new Int16Array(ch.length);
      for (let i = 0; i < ch.length; i++) {
        let s = Math.max(-1, Math.min(1, ch[i]));
        pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }
    return true;
  }
}
registerProcessor("mic-processor", MicProcessor);
