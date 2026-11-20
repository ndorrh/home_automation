# Smart Home AV Monitor

Self-contained remote microphone, webcam, and screen capture server for Windows, accessible on your home Wi-Fi or remotely over an isolated Cloudflare Quick Tunnel (`cloudflared`), with Windows DPAPI-encrypted Gmail notifications and password-protected encrypted recordings at rest.

## Features
- **Self-Contained Portable Setup:** Double-click `run.bat` on this PC or after copying the folder to a second Windows PC—it automatically configures `.venv`, downloads `bin/cloudflared.exe` if missing, installs `mailer` dependencies, registers the `SmartHomeAVServer` Scheduled Task, and launches silently in the background.
- **Encrypted Recordings at Rest (`.enc`):** Audio (`.wav.enc`), snapshots/screen images (`.jpg.enc`), and videos (`.mp4.enc`) are encrypted with X25519 + AES-256-GCM. Files cannot be opened directly in Windows Explorer without signing into the web dashboard (or running `scripts/decrypt_recording.py`) with your password.
- **DPAPI-Protected Gmail Credentials:** Gmail sender/app-password/recipient settings are sealed in `.env` (`GMAIL_CREDENTIALS_ENC`) and decrypted only in memory when sending notifications.
- **Connectivity Watchdog & Offline Hotspot Failover:** Continuously monitors internet connectivity, emails your LAN + `trycloudflare.com` URL via Nodemailer once online, and automatically turns on your PC's Wi-Fi Hotspot when offline (or toggle manually from the dashboard).
- **Dashboard System Panel:** View live `logs/server.log`, inspect Firewall / Task / Hotspot status, run allowlisted maintenance scripts remotely, hide/unhide `.env`, and read the built-in portability guide.
- **`.env` Protection:** Automatically hides and locks `.env` (`+H +S +R` + owner-only ACL); toggleable from the dashboard or `scripts/protect_env.ps1`.
- **Persistent Scheduling:** Schedule one-time, daily, or interval recordings for Microphone, Camera, and Screen directly from the web dashboard (`data/schedules.json`).
- **Screen Capture & Webcam:** Live MJPEG preview, snapshots, and MP4 recording for both webcam and desktop monitors.
- **Manual Device Release:** Click **Force Release Devices** in the dashboard to close conflicting desktop apps holding the mic/camera.

## Running & Checking Background Status
- **Start silently in background (auto-configures on first run):** Double-click `run.bat` (the terminal closes automatically).
- **Confirm it is working anytime:**
  1. Double-click `check_status.bat` (shows background process PIDs, Scheduled Task state, active LAN/tunnel URLs, and latest logs).
  2. Open `http://localhost:8000` in your browser.
  3. View [`logs/server.log`](logs/server.log).
  4. Check **Task Manager** for `pythonw.exe` and `cloudflared.exe`, or **Task Scheduler** for `SmartHomeAVServer`.
- **Foreground console mode (for debugging):** Double-click `run_console.bat`.

## Configuring Gmail or Changing Password
- **Set/rotate Gmail credentials (sealed with Windows DPAPI):**
  ```powershell
  .\.venv\Scripts\python.exe .\scripts\set_gmail.py
  ```
- **Change dashboard login password (re-wraps encryption key so existing `.enc` files remain readable):**
  ```powershell
  .\.venv\Scripts\python.exe .\scripts\set_password.py
  ```
- **Decrypt a local `.enc` file via CLI:**
  ```powershell
  .\.venv\Scripts\python.exe .\scripts\decrypt_recording.py recordings\audio\example.wav.enc
  ```

## Uninstall Background Task
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall_task.ps1
```

## Run Tests
```powershell
.\.venv\Scripts\pytest.exe -v
```
