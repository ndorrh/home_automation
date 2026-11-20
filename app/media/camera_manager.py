"""Thread-safe webcam capture for MJPEG streaming and encrypted JPG/MP4 recordings."""

from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np

from app.crypto import encrypt_bytes
from app.errors import DeviceUnavailableError
from app.media.recordings import encrypt_and_cleanup

logger = logging.getLogger("smart_home_av.camera")


class CameraManager:
    """Manages shared OpenCV VideoCapture, MJPEG stream, and encrypted JPG/MP4 storage."""

    def __init__(
        self,
        recordings_dir: Path,
        camera_index: int = 0,
        enc_public_key: str = "",
    ) -> None:
        self._snap_dir = recordings_dir / "snapshots"
        self._video_dir = recordings_dir / "video"
        self._camera_index = camera_index
        self._enc_public_key = enc_public_key
        self._lock = threading.Lock()
        self._frame_cond = threading.Condition(self._lock)

        self._cap: cv2.VideoCapture | None = None
        self._worker: threading.Thread | None = None
        self._running = False
        self._stream_clients = 0

        self._latest_frame: np.ndarray | None = None
        self._latest_jpeg: bytes | None = None
        self._frame_seq = 0

        self._writer: cv2.VideoWriter | None = None
        self._tmp_video_path: Path | None = None
        self._final_video_path: Path | None = None

    def status(self) -> dict:
        with self._lock:
            return {
                "active": self._running,
                "stream_clients": self._stream_clients,
                "recording": self._writer is not None,
                "recording_file": self._final_video_path.name if self._final_video_path else None,
            }

    def take_snapshot(self) -> str:
        """Capture one frame and encrypt directly to .jpg.enc."""
        frame = self._grab_single_frame()
        self._snap_dir.mkdir(parents=True, exist_ok=True)
        filename = f"snap_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg.enc"
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        if not ok:
            raise DeviceUnavailableError("Failed to encode snapshot frame.")
        encrypt_bytes(encoded.tobytes(), self._snap_dir / filename, self._enc_public_key)
        logger.info("Saved encrypted snapshot: %s", filename)
        return filename

    def start_video_recording(self) -> str:
        """Start saving webcam frames to a temporary MP4 before encrypting."""
        with self._lock:
            if self._writer is not None and self._final_video_path is not None:
                return self._final_video_path.name
            self._start_worker_locked()

        frame = self._wait_for_first_frame(timeout=5.0)
        height, width = frame.shape[:2]

        with self._lock:
            if self._writer is not None and self._final_video_path is not None:
                return self._final_video_path.name
            self._video_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            tmp_target = self._video_dir / f".tmp_vid_{stamp}.mp4"
            enc_target = self._video_dir / f"vid_{stamp}.mp4.enc"
            writer = cv2.VideoWriter(
                str(tmp_target), cv2.VideoWriter_fourcc(*"mp4v"), 15.0, (width, height)
            )
            if not writer.isOpened():
                self._stop_if_idle_locked()
                raise DeviceUnavailableError("Failed to initialize MP4 video writer.")
            self._writer = writer
            self._tmp_video_path = tmp_target
            self._final_video_path = enc_target

        logger.info("Started encrypted MP4 recording: %s", enc_target.name)
        return enc_target.name

    def stop_video_recording(self) -> str | None:
        """Stop MP4 recording, encrypt to .mp4.enc, and release webcam if idle."""
        with self._lock:
            tmp_path = self._tmp_video_path
            enc_path = self._final_video_path
            self._close_writer_locked()
            self._stop_if_idle_locked()

        if tmp_path and enc_path and tmp_path.exists():
            encrypt_and_cleanup(tmp_path, enc_path, self._enc_public_key)
            logger.info("Saved encrypted MP4 recording: %s", enc_path.name)
            return enc_path.name
        return None

    def mjpeg_generator(self) -> Iterator[bytes]:
        """Yield multipart/x-mixed-replace JPEG frames for an authenticated viewer."""
        with self._lock:
            self._stream_clients += 1
            self._start_worker_locked()

        last_seq = -1
        try:
            while True:
                jpeg, last_seq = self._next_jpeg_frame(last_seq, timeout=3.0)
                if jpeg is None:
                    continue
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
        finally:
            with self._lock:
                self._stream_clients = max(0, self._stream_clients - 1)
                self._stop_if_idle_locked()

    def shutdown(self) -> None:
        self.stop_video_recording()
        with self._lock:
            self._stream_clients = 0
            self._stop_worker_locked()

    def _grab_single_frame(self) -> np.ndarray:
        with self._lock:
            if self._running and self._latest_frame is not None:
                return self._latest_frame.copy()

        cap = self._open_capture()
        try:
            for _ in range(4):
                ok, frame = cap.read()
                if ok and frame is not None:
                    return frame
            raise DeviceUnavailableError("Camera opened but failed to return a frame.")
        finally:
            cap.release()

    def _open_capture(self) -> cv2.VideoCapture:
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(self._camera_index, backend)
        if not cap.isOpened():
            cap.release()
            raise DeviceUnavailableError(
                "Webcam is unavailable or currently used by another application."
            )
        return cap

    def _start_worker_locked(self) -> None:
        if self._running:
            return
        cap = self._open_capture()
        self._cap = cap
        self._running = True
        self._latest_frame = None
        self._latest_jpeg = None
        worker = threading.Thread(target=self._capture_loop, daemon=True)
        self._worker = worker
        worker.start()
        logger.info("Webcam capture worker started.")

    def _stop_if_idle_locked(self) -> None:
        if self._stream_clients == 0 and self._writer is None:
            self._stop_worker_locked()

    def _stop_worker_locked(self) -> None:
        self._running = False
        self._frame_cond.notify_all()
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception as exc:
                logger.warning("Error releasing VideoCapture: %s", exc)
            self._cap = None
        self._worker = None
        logger.info("Webcam released.")

    def _close_writer_locked(self) -> None:
        if self._writer is not None:
            try:
                self._writer.release()
            except Exception as exc:
                logger.warning("Error closing VideoWriter: %s", exc)
        self._writer = None
        self._tmp_video_path = None
        self._final_video_path = None

    def _wait_for_first_frame(self, timeout: float) -> np.ndarray:
        deadline = time.monotonic() + timeout
        with self._lock:
            while self._running and self._latest_frame is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._frame_cond.wait(timeout=remaining)
            if self._latest_frame is None:
                raise DeviceUnavailableError("Timed out waiting for webcam frame.")
            return self._latest_frame.copy()

    def _next_jpeg_frame(self, last_seq: int, timeout: float) -> tuple[bytes | None, int]:
        with self._lock:
            if not self._running:
                return None, last_seq
            if self._frame_seq == last_seq:
                self._frame_cond.wait(timeout=timeout)
            if not self._running or self._latest_jpeg is None:
                return None, last_seq
            return self._latest_jpeg, self._frame_seq

    def _capture_loop(self) -> None:
        while True:
            with self._lock:
                if not self._running or self._cap is None:
                    return
                cap = self._cap

            ok, frame = cap.read()
            if not ok or frame is None:
                time.sleep(0.05)
                continue

            with self._lock:
                need_jpeg = self._stream_clients > 0

            jpeg_bytes = None
            if need_jpeg:
                encoded_ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                jpeg_bytes = buf.tobytes() if encoded_ok else None

            with self._lock:
                self._latest_frame = frame
                if jpeg_bytes is not None:
                    self._latest_jpeg = jpeg_bytes
                if self._writer is not None:
                    self._writer.write(frame)
                self._frame_seq += 1
                self._frame_cond.notify_all()

            time.sleep(1.0 / 15.0)
