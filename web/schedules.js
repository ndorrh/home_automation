window.initScheduleModule = function initScheduleModule({ apiFetch, showToast }) {
  const form = document.getElementById("schedule-form");
  const listEl = document.getElementById("schedules-list");
  const modeEl = document.getElementById("sched-mode");
  const timeEl = document.getElementById("sched-time");
  const intervalEl = document.getElementById("sched-interval");
  const runAtEl = document.getElementById("sched-runat");
  const targetEl = document.getElementById("sched-target");
  const durationEl = document.getElementById("sched-duration");
  const nameEl = document.getElementById("sched-name");

  function syncModeInputs() {
    const mode = modeEl.value;
    timeEl.classList.toggle("hidden", mode !== "daily");
    intervalEl.classList.toggle("hidden", mode !== "interval");
    runAtEl.classList.toggle("hidden", mode !== "once");
    const isSnap = targetEl.value.endsWith("snapshot");
    durationEl.classList.toggle("hidden", isSnap);
  }

  function renderScheduleRow(item) {
    const row = document.createElement("div");
    row.className = "rec-item";

    const meta = document.createElement("div");
    meta.className = "rec-meta";
    const title = document.createElement("strong");
    title.textContent = `${item.name} (${item.target})`;
    const sub = document.createElement("span");
    sub.className = "muted small";
    const nextText = item.next_run_at ? new Date(item.next_run_at).toLocaleString() : "Paused";
    sub.textContent = `${item.mode} | Next: ${nextText} | Last: ${item.last_status}`;
    meta.append(title, sub);

    const actions = document.createElement("div");
    actions.className = "rec-actions";

    const toggle = document.createElement("button");
    toggle.className = "btn btn-secondary";
    toggle.type = "button";
    toggle.textContent = item.enabled ? "Disable" : "Enable";
    toggle.addEventListener("click", async () => {
      try {
        await apiFetch(`/api/schedules/${item.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: !item.enabled }),
        });
        await loadSchedules();
      } catch (err) {
        showToast(err.message, "error");
      }
    });

    const del = document.createElement("button");
    del.className = "btn btn-ghost";
    del.type = "button";
    del.textContent = "Delete";
    del.addEventListener("click", async () => {
      try {
        await apiFetch(`/api/schedules/${item.id}`, { method: "DELETE" });
        await loadSchedules();
      } catch (err) {
        showToast(err.message, "error");
      }
    });

    actions.append(toggle, del);
    row.append(meta, actions);
    return row;
  }

  async function loadSchedules() {
    const data = await apiFetch("/api/schedules");
    const items = data.items || [];
    listEl.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "No schedules configured.";
      listEl.appendChild(empty);
      return;
    }
    for (const item of items) {
      listEl.appendChild(renderScheduleRow(item));
    }
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const mode = modeEl.value;
    const payload = {
      name: nameEl.value.trim(),
      target: targetEl.value,
      mode,
      duration_seconds: Number(durationEl.value || 30),
      time_of_day: mode === "daily" ? timeEl.value : null,
      interval_minutes: mode === "interval" ? Number(intervalEl.value || 30) : null,
      run_at: mode === "once" && runAtEl.value ? new Date(runAtEl.value).toISOString() : null,
    };
    try {
      await apiFetch("/api/schedules", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      nameEl.value = "";
      showToast("Schedule saved.");
      await loadSchedules();
    } catch (err) {
      showToast(err.message, "error");
    }
  });

  modeEl.addEventListener("change", syncModeInputs);
  targetEl.addEventListener("change", syncModeInputs);
  syncModeInputs();
  return { loadSchedules };
};
