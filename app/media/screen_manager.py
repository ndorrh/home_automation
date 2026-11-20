"""Thread-safe desktop screen capture for MJPEG live viewing and encrypted JPG/MP4 storage."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Iterator

import cv2
import mss
import numpy as np

from app.crypto import encrypt_bytes
from app.errors import AppError, DeviceUnavailableError
from app.media.recordings import encrypt_and_cleanup

logger = logging.getLogger("smart_home_av.screen")


class ScreenManager:
    """Manages desktop screen capture via mss for live view, snapshots, and MP4 recording."""

    def __init__(
        self,
        recordings_dir: Path,
        default_monitor: int = 1,
        fps: int = 6,
        enc_public_key: str = "",
    ) -> None:
        self._screen_dir = recordings_dir / "screen"
        self._monitor_index = default_monitor
        self._fps = max(1, min(fps, 30))
        self._enc_public_key = enc_public_key
        self._lock = threading.Lock()
        self._frame_cond = threading.Condition(self._lock)

        self._running = False
        self._worker: threading.Thread | None = None
        self._stream_clients = 0
        self._latest_jpeg: bytes | None = None
        self._frame_seq = 0

        self._writer: cv2.VideoWriter | None = None
        self._tmp_video_path: Path | None = None
        self._final_video_path: Path | None = None
        self._writer_size: tuple[int, int] | None = None

    def list_monitors(self) -> list[dict]:
        """Return available monitors (index 1..N plus 0=all combined)."""
        with mss.mss() as sct:
            return [
                {
                    "index": idx,
                    "width": mon["width"],
                    "height": mon["height"],
                    "label": "All Monitors" if idx == 0 else f"Monitor {idx} ({mon['width']}x{mon['height']})",
                }
                for idx, mon in enumerate(sct.monitors)
            ]

    def status(self) -> dict:
        with self._lock:
            return {
                "active": self._running,
                "monitor_index": self._monitor_index,
                "stream_clients": self._stream_clients,
                "recording": self._writer is not None,
                "recording_file": self._final_video_path.name if self._final_video_path else None,
            }

    def set_monitor(self, monitor_index: int) -> None:
        if monitor_index < 0:
            raise AppError("INVALID_MONITOR", "Monitor index must be non-negative.", 400)
        with self._lock:
            self._monitor_index = monitor_index

    def take_screenshot(self, monitor_index: int | None = None) -> str:
        """Capture one desktop frame and encrypt directly to .jpg.enc."""
        idx = self._monitor_index if monitor_index is None else monitor_index
        bgr = self._grab_bgr_once(idx)
        self._screen_dir.mkdir(parents=True, exist_ok=True)
        filename = f"screen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg.enc"
        ok, encoded = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        if not ok:
            raise DeviceUnavailableError("Failed to encode desktop screenshot.")
        encrypt_bytes(encoded.tobytes(), self._screen_dir / filename, self._enc_public_key)
        logger.info("Saved encrypted screenshot: %s", filename)
        return filename

    def start_recording(self, monitor_index: int | None = None) -> str:
        """Start recording desktop screen frames to encrypted MP4."""
        if monitor_index is not None:
            self.set_monitor(monitor_index)

        with self._lock:
            if self._writer is not None and self._final_video_path is not None:
                return self._final_video_path.name

        sample = self._grab_bgr_once(self._monitor_index)
        height, width = sample.shape[:2]

        with self._lock:
            if self._writer is not None and self._final_video_path is not None:
                return self._final_video_path.name
            self._screen_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            tmp_target = self._screen_dir / f".tmp_screenvid_{stamp}.mp4"
            enc_target = self._screen_dir / f"screenvid_{stamp}.mp4.enc"
            writer = cv2.VideoWriter(
                str(tmp_target), cv2.VideoWriter_fourcc(*"mp4v"), float(self._fps), (width, height)
            )
            if not writer.isOpened():
                raise DeviceUnavailableError("Failed to initialize screen MP4 writer.")
            self._writer = writer
            self._writer_size = (width, height)
            self._tmp_video_path = tmp_target
            self._final_video_path = enc_target
            self._start_worker_locked()

        logger.info("Started encrypted screen recording: %s", enc_target.name)
        return enc_target.name

    def stop_recording(self) -> str | None:
        """Stop screen MP4 recording and encrypt output to .mp4.enc."""
        with self._lock:
            tmp_path = self._tmp_video_path
            enc_path = self._final_video_path
            self._close_writer_locked()
            self._stop_if_idle_locked()

        if tmp_path and enc_path and tmp_path.exists():
            encrypt_and_cleanup(tmp_path, enc_path, self._enc_public_key)
            logger.info("Saved encrypted screen recording: %s", enc_path.name)
            return enc_path.name
        return None

    def mjpeg_generator(self, monitor_index: int | None = None) -> Iterator[bytes]:
        """Yield MJPEG frames of the desktop screen for authenticated viewing."""
        if monitor_index is not None:
            self.set_monitor(monitor_index)
        with self._lock:
            self._stream_clients += 1
            self._start_worker_locked()

        last_seq = -1
        try:
            while True:
                jpeg, last_seq = self._next_jpeg(last_seq, timeout=3.0)
                if jpeg is None:
                    continue
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
        finally:
            with self._lock:
                self._stream_clients = max(0, self._stream_clients - 1)
                self._stop_if_idle_locked()

    def shutdown(self) -> None:
        self.stop_recording()
        with self._lock:
            self._stream_clients = 0
            self._running = False
            self._frame_cond.notify_all()

    def _grab_bgr_once(self, monitor_index: int) -> np.ndarray:
        try:
            with mss.mss() as sct:
                idx = monitor_index if monitor_index < len(sct.monitors) else 1
                shot = sct.grab(sct.monitors[idx])
                return cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
        except Exception as exc:
            raise DeviceUnavailableError(
                f"Screen capture unavailable (Windows desktop may be locked): {exc}"
            ) from exc

    def _start_worker_locked(self) -> None:
        if self._running:
            return
        self._running = True
        worker = threading.Thread(target=self._capture_loop, daemon=True)
        self._worker = worker
        worker.start()

    def _stop_if_idle_locked(self) -> None:
        if self._stream_clients == 0 and self._writer is None:
            self._running = False
            self._frame_cond.notify_all()
            self._worker = None

    def _close_writer_locked(self) -> None:
        if self._writer is not None:
            try:
                self._writer.release()
            except Exception as exc:
                logger.warning("Error closing screen writer: %s", exc)
        self._writer = None
        self._writer_size = None
        self._tmp_video_path = None
        self._final_video_path = None

    def _next_jpeg(self, last_seq: int, timeout: float) -> tuple[bytes | None, int]:
        with self._lock:
            if not self._running:
                return None, last_seq
            if self._frame_seq == last_seq:
                self._frame_cond.wait(timeout=timeout)
            if not self._running or self._latest_jpeg is None:
                return None, last_seq
            return self._latest_jpeg, self._frame_seq

    def _capture_loop(self) -> None:
        interval = 1.0 / float(self._fps)
        with mss.mss() as sct:
            while True:
                with self._lock:
                    if not self._running:
                        return
                    idx = self._monitor_index if self._monitor_index < len(sct.monitors) else 1

                try:
                    shot = sct.grab(sct.monitors[idx])
                    bgr = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
                except Exception as exc:
                    logger.warning("Screen frame capture skipped: %s", exc)
                    time.sleep(1.0)
                    continue
                with self._lock:
                    need_jpeg = self._stream_clients > 0

                jpeg = None
                if need_jpeg:
                    ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
                    jpeg = buf.tobytes() if ok else None

                with self._lock:
                    if jpeg is not None:
                        self._latest_jpeg = jpeg
                    if self._writer is not None and self._writer_size is not None:
                        frame = bgr
                        if (bgr.shape[1], bgr.shape[0]) != self._writer_size:
                            frame = cv2.resize(bgr, self._writer_size)
                        self._writer.write(frame)
                    self._frame_seq += 1
                    self._frame_cond.notify_all()

                time.sleep(interval)
