window.initSystemModule = function initSystemModule({ apiFetch, showToast }) {
  const pidBadge = document.getElementById("sys-pid-badge");
  const taskBadge = document.getElementById("sys-task-badge");
  const fwBadge = document.getElementById("sys-fw-badge");
  const hotspotBadge = document.getElementById("sys-hotspot-badge");
  const envBadge = document.getElementById("sys-env-badge");
  const scriptsEl = document.getElementById("sys-scripts-list");
  const outputEl = document.getElementById("sys-script-output");
  const logsEl = document.getElementById("sys-logs-box");
  const refreshBtn = document.getElementById("sys-refresh-btn");
  const clearLogsBtn = document.getElementById("sys-clear-logs-btn");
  const autoCheck = document.getElementById("sys-log-auto");
  let logTimer = null;

  function renderOverview(data) {
    pidBadge.textContent = `PID: ${data.pid ?? "--"}`;
    taskBadge.textContent = `Task: ${data.task_state || "--"}`;
    taskBadge.className = `badge ${data.task_state === "Ready" || data.task_state === "Running" ? "badge-ok" : "badge-warn"}`;

    fwBadge.textContent = `Firewall: ${data.firewall_active ? "Open" : "Blocked/Missing"}`;
    fwBadge.className = `badge ${data.firewall_active ? "badge-ok" : "badge-warn"}`;

    const hs = data.hotspot || {};
    hotspotBadge.textContent = hs.active ? `Hotspot: ON (${hs.ssid || "AP"})` : "Hotspot: OFF";
    hotspotBadge.className = `badge ${hs.active ? "badge-ok" : "badge-info"}`;

    envBadge.textContent = `.env: ${data.env_protected ? "Hidden & Locked" : "Visible"}`;
    envBadge.className = `badge ${data.env_protected ? "badge-ok" : "badge-warn"}`;
  }

  function renderScriptCard(item) {
    const card = document.createElement("div");
    card.className = "script-card";

    const info = document.createElement("div");
    const title = document.createElement("strong");
    title.className = "small";
    title.textContent = item.label;
    const desc = document.createElement("p");
    desc.className = "muted small";
    desc.textContent = item.description;
    info.append(title, desc);

    const btn = document.createElement("button");
    btn.className = "btn btn-secondary";
    btn.type = "button";
    btn.textContent = "Run Remotely";
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const res = await apiFetch("/api/system/scripts/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ script_id: item.id }),
        });
        outputEl.classList.remove("hidden");
        outputEl.textContent = `[${item.id}] exit=${res.exit_code}\n${res.stdout || ""}${res.stderr ? "\nERR: " + res.stderr : ""}`;
        showToast(res.success ? `Completed ${item.id}` : `${item.id} returned code ${res.exit_code}`, res.success ? "ok" : "error");
        if (res.restarting) {
          showToast("Server restarting... reconnecting shortly.");
          setTimeout(() => refreshAll().catch(() => {}), 4500);
          return;
        }
        await refreshAll();
      } catch (err) {
        showToast(err.message, "error");
      } finally {
        btn.disabled = false;
      }
    });

    card.append(info, btn);
    return card;
  }

  async function loadScripts() {
    const data = await apiFetch("/api/system/scripts");
    scriptsEl.replaceChildren();
    for (const item of data.items || []) {
      scriptsEl.appendChild(renderScriptCard(item));
    }
  }

  async function refreshAll() {
    const [overview, logs] = await Promise.all([
      apiFetch("/api/system/overview"),
      apiFetch("/api/system/logs?lines=120"),
    ]);
    renderOverview(overview);
    const lines = logs.lines || [];
    logsEl.textContent = lines.length ? lines.join("\n") : "No logs written yet.";
    logsEl.scrollTop = logsEl.scrollHeight;
  }

  function startLogPolling() {
    if (logTimer) clearInterval(logTimer);
    logTimer = setInterval(() => {
      if (autoCheck.checked && !document.getElementById("dashboard-view").classList.contains("hidden")) {
        refreshAll().catch(() => {});
      }
    }, 5000);
  }

  refreshBtn.addEventListener("click", () => refreshAll().catch((e) => showToast(e.message, "error")));
  clearLogsBtn?.addEventListener("click", async () => {
    try {
      await apiFetch("/api/system/logs/clear", { method: "POST" });
      showToast("Server logs cleared.");
      await refreshAll();
    } catch (e) {
      showToast(e.message, "error");
    }
  });
  startLogPolling();

  return {
    init: async () => {
      await Promise.all([loadScripts(), refreshAll()]);
    },
  };
};
