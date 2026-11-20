import nodemailer from "nodemailer";

function readStdin() {
  return new Promise((resolve, reject) => {
    let raw = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      raw += chunk;
    });
    process.stdin.on("end", () => resolve(raw));
    process.stdin.on("error", reject);
  });
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function buildHtml(payload) {
  const lanList = (payload.lanUrls || [])
    .map((u) => `<li><a href="${escapeHtml(u)}">${escapeHtml(u)}</a></li>`)
    .join("");
  const tunnelSection = payload.tunnelUrl
    ? `<p><strong>Remote Access (Cloudflare Tunnel):</strong><br><a href="${escapeHtml(payload.tunnelUrl)}">${escapeHtml(payload.tunnelUrl)}</a></p>`
    : "<p><strong>Remote Access:</strong> Tunnel URL not active yet.</p>";

  return `
    <div style="font-family:Segoe UI,Arial,sans-serif;line-height:1.5;color:#1f2937;">
      <h2 style="margin:0 0 12px;">Smart Home AV Server Online</h2>
      <p>Your PC audio &amp; camera server is running as of <strong>${escapeHtml(payload.timestamp || "")}</strong>.</p>
      ${tunnelSection}
      <p><strong>Local Wi-Fi URLs:</strong></p>
      <ul>${lanList || "<li>Unavailable</li>"}</ul>
      <p style="font-size:12px;color:#6b7280;">Sign in with your configured dashboard password.</p>
    </div>
  `.trim();
}

function buildText(payload) {
  const lanLines = (payload.lanUrls || []).map((u) => `  - ${u}`).join("\n");
  return [
    "Smart Home AV Server Online",
    `Timestamp: ${payload.timestamp || ""}`,
    `Remote Tunnel URL: ${payload.tunnelUrl || "Unavailable"}`,
    "Local Wi-Fi URLs:",
    lanLines || "  - Unavailable",
  ].join("\n");
}

async function main() {
  const user = (process.env.GMAIL_USER || "").trim();
  const pass = (process.env.GMAIL_APP_PASSWORD || "").trim();
  const to = (process.env.NOTIFY_EMAIL_TO || user).trim();

  if (!user || !pass || !to) {
    console.error("Missing GMAIL_USER, GMAIL_APP_PASSWORD, or NOTIFY_EMAIL_TO.");
    process.exit(2);
  }

  const raw = await readStdin();
  const payload = JSON.parse(raw || "{}");
  const transporter = nodemailer.createTransport({
    host: "smtp.gmail.com",
    port: 465,
    secure: true,
    auth: { user, pass },
  });

  const info = await transporter.sendMail({
    from: `"Smart Home AV" <${user}>`,
    to,
    subject: payload.tunnelUrl
      ? "Smart Home AV Server Online (Remote URL Ready)"
      : "Smart Home AV Server Online (Local Wi-Fi)",
    text: buildText(payload),
    html: buildHtml(payload),
  });

  console.log(JSON.stringify({ ok: true, messageId: info.messageId }));
}

main().catch((err) => {
  console.error(err?.message || String(err));
  process.exit(1);
});
