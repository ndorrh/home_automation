window.createPcmPlayer = function createPcmPlayer(levelBarEl) {
  let audioCtx = null;
  let nextPlayTime = 0;
  let active = false;

  async function start(sampleRate) {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    audioCtx = new AudioCtx({ sampleRate });
    await audioCtx.resume();
    nextPlayTime = 0;
    active = true;
  }

  async function stop() {
    active = false;
    if (levelBarEl) levelBarEl.style.width = "0%";
    if (audioCtx) {
      await audioCtx.close().catch(() => {});
      audioCtx = null;
    }
  }

  function push(arrayBuffer, sampleRate) {
    if (!audioCtx || !active) return;
    const int16 = new Int16Array(arrayBuffer);
    if (int16.length === 0) return;

    const float32 = new Float32Array(int16.length);
    let peak = 0;
    for (let i = 0; i < int16.length; i += 1) {
      const val = int16[i] / 32768;
      float32[i] = val;
      if (Math.abs(val) > peak) peak = Math.abs(val);
    }
    if (levelBarEl) levelBarEl.style.width = `${Math.min(100, Math.round(peak * 180))}%`;

    const buf = audioCtx.createBuffer(1, float32.length, sampleRate);
    buf.copyToChannel(float32, 0);
    const src = audioCtx.createBufferSource();
    src.buffer = buf;
    src.connect(audioCtx.destination);
    const now = audioCtx.currentTime;
    if (nextPlayTime < now || nextPlayTime > now + 0.35) {
      nextPlayTime = now + 0.03;
    }
    src.start(nextPlayTime);
    nextPlayTime += buf.duration;
  }

  return { start, stop, push };
};
