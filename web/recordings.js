window.initRecordingsModule = function initRecordingsModule({ apiFetch, showToast }) {
  const listEl = document.getElementById("recordings-list");
  let activeFilter = "";

  function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  }

  function buildPreview(item) {
    if (item.kind === "audio") {
      const audio = document.createElement("audio");
      audio.controls = true;
      audio.preload = "none";
      audio.className = "rec-preview";
      audio.src = item.url;
      return audio;
    }
    if (item.media_type === "image/jpeg" || item.media_type === "image/png") {
      const img = document.createElement("img");
      img.className = "rec-thumb";
      img.src = item.url;
      img.alt = item.display_name || item.filename;
      return img;
    }
    return null;
  }

  function buildRow(item) {
    const row = document.createElement("div");
    row.className = "rec-item";

    const meta = document.createElement("div");
    meta.className = "rec-meta";
    const title = document.createElement("strong");
    title.textContent = item.display_name || item.filename;
    const sub = document.createElement("span");
    sub.className = "muted small";
    sub.textContent = `${item.kind} | ${formatBytes(item.size_bytes)} | encrypted`;
    meta.append(title, sub);

    const actions = document.createElement("div");
    actions.className = "rec-actions";
    const preview = buildPreview(item);
    if (preview) actions.appendChild(preview);

    const dl = document.createElement("a");
    dl.className = "btn btn-secondary";
    dl.href = `${item.url}?download=1`;
    dl.download = item.display_name || item.filename;
    dl.textContent = "Download";

    const del = document.createElement("button");
    del.className = "btn btn-ghost";
    del.type = "button";
    del.textContent = "Delete";
    del.addEventListener("click", async () => {
      try {
        await apiFetch(`/api/recordings/${item.kind}/${encodeURIComponent(item.filename)}`, {
          method: "DELETE",
        });
        showToast(`Deleted ${item.display_name || item.filename}`);
        await loadRecordings();
      } catch (err) {
        showToast(err.message, "error");
      }
    });

    actions.append(dl, del);
    row.append(meta, actions);
    return row;
  }

  async function loadRecordings() {
    const qs = activeFilter ? `?kind=${encodeURIComponent(activeFilter)}` : "";
    const data = await apiFetch(`/api/recordings${qs}`);
    const items = data.items || [];
    listEl.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "No recordings found.";
      listEl.appendChild(empty);
      return;
    }
    for (const item of items) {
      listEl.appendChild(buildRow(item));
    }
  }

  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      btn.classList.add("active");
      activeFilter = btn.dataset.kind || "";
      loadRecordings().catch((err) => showToast(err.message, "error"));
    });
  });

  return { loadRecordings };
};
