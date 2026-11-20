const state = {
  ws: null,
  listening: false,
  micRecording: false,
  camStreaming: false,
  camRecording: false,
  screenStreaming: false,
  screenRecording: false,
  sampleRate: 16000,
  pcmPlayer: null,
  scheduleApi: null,
  recordingsApi: null,
  systemApi: null,
};

const els = {
  loginView: document.getElementById("login-view"),
  dashboardView: document.getElementById("dashboard-view"),
  loginForm: document.getElementById("login-form"),
  passwordInput: document.getElementById("password-input"),
  loginError: document.getElementById("login-error"),
  logoutBtn: document.getElementById("logout-btn"),
  releaseBtn: document.getElementById("release-devices-btn"),
  wsBadge: document.getElementById("ws-badge"),
  powerBadge: document.getElementById("power-badge"),
  lanUrls: document.getElementById("lan-urls"),
  tunnelUrl: document.getElementById("tunnel-url"),
  micStateBadge: document.getElementById("mic-state-badge"),
  micLevelBar: document.getElementById("mic-level-bar"),
  micListenBtn: document.getElementById("mic-listen-btn"),
  micRecordBtn: document.getElementById("mic-record-btn"),
  micRecLabel: document.getElementById("mic-recording-label"),
  camStateBadge: document.getElementById("cam-state-badge"),
  camFeed: document.getElementById("cam-feed"),
  camFrame: document.getElementById("cam-frame"),
  camPlaceholder: document.getElementById("cam-placeholder"),
  camStreamBtn: document.getElementById("cam-stream-btn"),
  camSnapBtn: document.getElementById("cam-snap-btn"),
  camRecordBtn: document.getElementById("cam-record-btn"),
  camFsBtn: document.getElementById("cam-fs-btn"),
  camRecLabel: document.getElementById("cam-recording-label"),
  screenStateBadge: document.getElementById("screen-state-badge"),
  screenFeed: document.getElementById("screen-feed"),
  screenFrame: document.getElementById("screen-frame"),
  screenPlaceholder: document.getElementById("screen-placeholder"),
  monitorSelect: document.getElementById("monitor-select"),
  screenStreamBtn: document.getElementById("screen-stream-btn"),
  screenSnapBtn: document.getElementById("screen-snap-btn"),
  screenRecordBtn: document.getElementById("screen-record-btn"),
  screenFsBtn: document.getElementById("screen-fs-btn"),
  screenRecLabel: document.getElementById("screen-recording-label"),
  toastContainer: document.getElementById("toast-container"),
};

function showToast(message, kind = "ok") {
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.textContent = message;
  els.toastContainer.appendChild(node);
  setTimeout(() => node.remove(), 4000);
}

async function apiFetch(url, options = {}) {
  const resp = await fetch(url, { credentials: "same-origin", ...options });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data?.error?.message || `Request failed (${resp.status})`);
  }
  return data;
}

function showView(authenticated) {
  els.loginView.classList.toggle("hidden", authenticated);
  els.dashboardView.classList.toggle("hidden", !authenticated);
}

function sendWs(action) {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    showToast("WebSocket is disconnected. Reconnecting...", "error");
    return false;
  }
  state.ws.send(JSON.stringify({ action }));
  return true;
}

function playPcmBuffer(arrayBuffer) {
  if (state.listening && state.pcmPlayer) {
    state.pcmPlayer.push(arrayBuffer, state.sampleRate);
  }
}

function renderLinks(container, urls, fallback) {
  container.replaceChildren();
  if (!urls.length) {
    container.textContent = fallback;
    return;
  }
  urls.forEach((url, idx) => {
    if (idx > 0) container.appendChild(document.createTextNode("  |  "));
    const a = document.createElement("a");
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = url;
    container.appendChild(a);
  });
}

function setupMainTabs() {
  const buttons = document.querySelectorAll(".nav-tab");
  const panels = document.querySelectorAll(".tab-panel");
  buttons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.tab;
      buttons.forEach((b) => {
        const isActive = b === btn;
        b.classList.toggle("active", isActive);
        b.setAttribute("aria-selected", String(isActive));
      });
      panels.forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== target));
    });
  });
}

function applyStatus(status) {
  if (!status) return;
  const audio = status.audio || {};
  const cam = status.camera || {};
  const scr = status.screen || {};
  const net = status.network || {};

  state.sampleRate = audio.sample_rate || 16000;
  state.micRecording = Boolean(audio.recording);
  state.camRecording = Boolean(cam.recording);
  state.screenRecording = Boolean(scr.recording);

  els.powerBadge.textContent = status.power?.keep_awake ? "Awake: On" : "Awake: Off";
  renderLinks(els.lanUrls, net.lan_urls || [], "None");
  renderLinks(els.tunnelUrl, net.tunnel_url ? [net.tunnel_url] : [], "Waiting for tunnel...");

  els.micRecordBtn.textContent = state.micRecording ? "Stop WAV Recording" : "Record WAV";
  els.micRecLabel.textContent = audio.recording_file ? `Recording: ${audio.recording_file}` : "Not recording";
  els.micStateBadge.textContent = state.listening ? "Listening" : state.micRecording ? "Recording" : "Idle";

  els.camRecordBtn.textContent = state.camRecording ? "Stop MP4 Recording" : "Record MP4";
  els.camRecLabel.textContent = cam.recording_file ? `Recording: ${cam.recording_file}` : "Not recording";
  els.camStateBadge.textContent = state.camStreaming ? "Live" : state.camRecording ? "Recording" : "Idle";

  els.screenRecordBtn.textContent = state.screenRecording ? "Stop Screen Rec" : "Record Screen";
  els.screenRecLabel.textContent = scr.recording_file ? `Recording: ${scr.recording_file}` : "Not recording";
  els.screenStateBadge.textContent = state.screenStreaming ? "Live" : state.screenRecording ? "Recording" : "Idle";
}

function connectWebSocket() {
  if (state.ws) state.ws.close();
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws`);
  ws.binaryType = "arraybuffer";
  state.ws = ws;

  ws.onopen = () => {
    els.wsBadge.textContent = "WS: Connected";
    els.wsBadge.className = "badge badge-ok";
  };

  ws.onmessage = (event) => {
    if (event.data instanceof ArrayBuffer) {
      playPcmBuffer(event.data);
      return;
    }
    const msg = JSON.parse(event.data);
    if (msg.type === "status") applyStatus(msg.data);
    if (msg.type === "mic_saved" || msg.type === "cam_saved" || msg.type === "screen_saved") {
      showToast(`Encrypted & saved ${msg.filename}`);
      state.recordingsApi?.loadRecordings();
    }
    if (msg.type === "schedule_ran") {
      showToast(`Schedule '${msg.name}' completed`);
      state.recordingsApi?.loadRecordings();
      state.scheduleApi?.loadSchedules();
    }
    if (msg.type === "error") showToast(msg.message, "error");
  };

  ws.onclose = () => {
    els.wsBadge.textContent = "WS: Reconnecting";
    els.wsBadge.className = "badge badge-warn";
    setTimeout(() => {
      if (!els.dashboardView.classList.contains("hidden")) connectWebSocket();
    }, 3000);
  };
}

async function loadMonitors() {
  const data = await apiFetch("/api/screen/monitors");
  els.monitorSelect.replaceChildren();
  for (const mon of data.monitors || []) {
    const opt = document.createElement("option");
    opt.value = String(mon.index);
    opt.textContent = mon.label;
    if (mon.index === 1) opt.selected = true;
    els.monitorSelect.appendChild(opt);
  }
}

async function initDashboard() {
  showView(true);
  connectWebSocket();
  if (!state.scheduleApi && window.initScheduleModule) {
    state.scheduleApi = window.initScheduleModule({ apiFetch, showToast });
  }
  if (!state.recordingsApi && window.initRecordingsModule) {
    state.recordingsApi = window.initRecordingsModule({ apiFetch, showToast });
  }
  if (!state.systemApi && window.initSystemModule) {
    state.systemApi = window.initSystemModule({ apiFetch, showToast });
  }
  try {
    const status = await apiFetch("/api/status");
    applyStatus(status);
    await Promise.all([
      loadMonitors(),
      state.recordingsApi?.loadRecordings(),
      state.scheduleApi?.loadSchedules(),
      state.systemApi?.init(),
    ]);
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function toggleListen() {
  if (!state.pcmPlayer && window.createPcmPlayer) {
    state.pcmPlayer = window.createPcmPlayer(els.micLevelBar);
  }
  if (!state.listening) {
    await state.pcmPlayer.start(state.sampleRate);
    if (sendWs("mic:listen_start")) {
      state.listening = true;
      els.micListenBtn.textContent = "Stop Listening";
    }
    return;
  }
  sendWs("mic:listen_stop");
  state.listening = false;
  els.micListenBtn.textContent = "Start Live Listen";
  await state.pcmPlayer?.stop();
}

function toggleCameraStream() {
  state.camStreaming = !state.camStreaming;
  if (state.camStreaming) {
    els.camFeed.src = `/api/camera/stream?t=${Date.now()}`;
    els.camFeed.classList.remove("hidden");
    els.camPlaceholder.classList.add("hidden");
    els.camStreamBtn.textContent = "Stop Live View";
    return;
  }
  els.camFeed.removeAttribute("src");
  els.camFeed.classList.add("hidden");
  els.camPlaceholder.classList.remove("hidden");
  els.camStreamBtn.textContent = "Start Live View";
}

function toggleScreenStream() {
  state.screenStreaming = !state.screenStreaming;
  if (state.screenStreaming) {
    const mon = encodeURIComponent(els.monitorSelect.value || "1");
    els.screenFeed.src = `/api/screen/stream?monitor=${mon}&t=${Date.now()}`;
    els.screenFeed.classList.remove("hidden");
    els.screenPlaceholder.classList.add("hidden");
    els.screenStreamBtn.textContent = "Stop Live Screen";
    return;
  }
  els.screenFeed.removeAttribute("src");
  els.screenFeed.classList.add("hidden");
  els.screenPlaceholder.classList.remove("hidden");
  els.screenStreamBtn.textContent = "Live Screen";
}

els.loginForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const password = els.passwordInput.value;
  if (!password) {
    els.loginError.textContent = "Password is required.";
    els.loginError.classList.remove("hidden");
    return;
  }
  els.loginError.classList.add("hidden");
  try {
    await apiFetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    els.passwordInput.value = "";
    await initDashboard();
  } catch (err) {
    els.loginError.textContent = err.message;
    els.loginError.classList.remove("hidden");
  }
});

els.logoutBtn.addEventListener("click", async () => {
  if (state.listening) await toggleListen();
  if (state.camStreaming) toggleCameraStream();
  if (state.screenStreaming) toggleScreenStream();
  await apiFetch("/api/logout", { method: "POST" });
  if (state.ws) state.ws.close();
  showView(false);
});

els.micListenBtn.addEventListener("click", () => toggleListen().catch((e) => showToast(e.message, "error")));
els.micRecordBtn.addEventListener("click", () => sendWs(state.micRecording ? "mic:record_stop" : "mic:record_start"));
els.camStreamBtn.addEventListener("click", toggleCameraStream);
els.camRecordBtn.addEventListener("click", () => sendWs(state.camRecording ? "cam:record_stop" : "cam:record_start"));
els.screenStreamBtn.addEventListener("click", toggleScreenStream);
els.screenRecordBtn.addEventListener("click", () => sendWs(state.screenRecording ? "screen:record_stop" : "screen:record_start"));

els.camSnapBtn.addEventListener("click", async () => {
  try {
    const res = await apiFetch("/api/camera/snapshot", { method: "POST" });
    showToast(`Saved ${res.filename}`);
    state.recordingsApi?.loadRecordings();
  } catch (err) {
    showToast(err.message, "error");
  }
});

els.screenSnapBtn.addEventListener("click", async () => {
  try {
    const monitor_index = Number(els.monitorSelect.value || 1);
    const res = await apiFetch("/api/screen/snapshot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ monitor_index }),
    });
    showToast(`Saved ${res.filename}`);
    state.recordingsApi?.loadRecordings();
  } catch (err) {
    showToast(err.message, "error");
  }
});

els.releaseBtn.addEventListener("click", async () => {
  try {
    const res = await apiFetch("/api/devices/release", { method: "POST" });
    const count = (res.terminated || []).length;
    showToast(count ? `Released ${count} conflicting app(s).` : "No conflicting apps running.");
  } catch (err) {
    showToast(err.message, "error");
  }
});

async function toggleFullscreen(frameEl) {
  try {
    if (document.fullscreenElement === frameEl) {
      await document.exitFullscreen();
    } else {
      await frameEl.requestFullscreen();
    }
  } catch (err) {
    showToast(`Fullscreen unavailable: ${err.message}`, "error");
  }
}

els.camFsBtn.addEventListener("click", () => toggleFullscreen(els.camFrame));
els.screenFsBtn.addEventListener("click", () => toggleFullscreen(els.screenFrame));
document.querySelectorAll("[data-fs-exit]").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  });
});

(async function boot() {
  setupMainTabs();
  try {
    await apiFetch("/api/auth/me");
    await initDashboard();
  } catch {
    showView(false);
  }
})();
