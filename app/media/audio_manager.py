"""Thread-safe microphone capture for WebSocket PCM streaming and encrypted WAV recording."""

from __future__ import annotations

import asyncio
import logging
import threading
import wave
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np
import sounddevice as sd

from app.errors import DeviceUnavailableError
from app.media.recordings import encrypt_and_cleanup

logger = logging.getLogger("smart_home_av.audio")


class AudioManager:
    """Manages shared microphone stream, WebSocket listeners, and encrypted WAV recording."""

    def __init__(
        self,
        recordings_dir: Path,
        sample_rate: int = 16000,
        enc_public_key: str = "",
    ) -> None:
        self._audio_dir = recordings_dir / "audio"
        self._sample_rate = sample_rate
        self._enc_public_key = enc_public_key
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None
        self._wave_file: wave.Wave_write | None = None
        self._tmp_path: Path | None = None
        self._final_enc_path: Path | None = None
        self._listeners: set[asyncio.Queue[bytes]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._level: float = 0.0
        self._status_cb: Callable[[], None] | None = None

    def bind_loop(
        self, loop: asyncio.AbstractEventLoop, status_cb: Callable[[], None] | None = None
    ) -> None:
        self._loop = loop
        self._status_cb = status_cb

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def status(self) -> dict:
        with self._lock:
            return {
                "streaming": self._stream is not None,
                "listening_clients": len(self._listeners),
                "recording": self._wave_file is not None,
                "recording_file": self._final_enc_path.name if self._final_enc_path else None,
                "sample_rate": self._sample_rate,
                "level": round(self._level, 3),
            }

    def add_listener(self) -> asyncio.Queue[bytes]:
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=24)
        with self._lock:
            self._listeners.add(queue)
            self._ensure_stream_locked()
        self._emit_status()
        return queue

    def remove_listener(self, queue: asyncio.Queue[bytes]) -> None:
        with self._lock:
            self._listeners.discard(queue)
            self._release_if_idle_locked()
        self._emit_status()

    def start_recording(self) -> str:
        with self._lock:
            if self._wave_file is not None and self._final_enc_path is not None:
                return self._final_enc_path.name
            self._audio_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            tmp_target = self._audio_dir / f".tmp_audio_{stamp}.wav"
            enc_target = self._audio_dir / f"audio_{stamp}.wav.enc"

            wf = wave.open(str(tmp_target), "wb")
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self._sample_rate)
            self._wave_file = wf
            self._tmp_path = tmp_target
            self._final_enc_path = enc_target
            try:
                self._ensure_stream_locked()
            except Exception:
                self._close_wave_locked()
                tmp_target.unlink(missing_ok=True)
                raise
        logger.info("Started encrypted WAV recording: %s", enc_target.name)
        self._emit_status()
        return enc_target.name

    def stop_recording(self) -> str | None:
        with self._lock:
            tmp_path = self._tmp_path
            enc_path = self._final_enc_path
            self._close_wave_locked()
            self._release_if_idle_locked()

        if tmp_path and enc_path and tmp_path.exists():
            encrypt_and_cleanup(tmp_path, enc_path, self._enc_public_key)
            logger.info("Saved encrypted WAV recording: %s", enc_path.name)
            self._emit_status()
            return enc_path.name

        self._emit_status()
        return None

    def shutdown(self) -> None:
        self.stop_recording()
        with self._lock:
            self._listeners.clear()
            self._stop_stream_locked()

    def _ensure_stream_locked(self) -> None:
        if self._stream is not None:
            return
        try:
            stream = sd.InputStream(
                samplerate=self._sample_rate,
                channels=1,
                dtype="int16",
                blocksize=1024,
                callback=self._audio_callback,
            )
            stream.start()
            self._stream = stream
            logger.info("Microphone stream opened at %d Hz.", self._sample_rate)
        except Exception as exc:
            raise DeviceUnavailableError(
                f"Microphone is unavailable or busy ({exc})."
            ) from exc

    def _release_if_idle_locked(self) -> None:
        if not self._listeners and self._wave_file is None:
            self._stop_stream_locked()

    def _stop_stream_locked(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        except Exception as exc:
            logger.warning("Error closing microphone stream: %s", exc)
        finally:
            self._stream = None
            self._level = 0.0
            logger.info("Microphone stream released.")

    def _close_wave_locked(self) -> None:
        if self._wave_file is not None:
            try:
                self._wave_file.close()
            except Exception as exc:
                logger.warning("Error closing WAV file: %s", exc)
        self._wave_file = None
        self._tmp_path = None
        self._final_enc_path = None

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            logger.debug("Audio input status: %s", status)
        pcm_bytes = indata.tobytes()
        peak = float(np.max(np.abs(indata))) / 32768.0 if frames else 0.0

        with self._lock:
            self._level = peak
            if self._wave_file is not None:
                self._wave_file.writeframes(pcm_bytes)
            listeners = list(self._listeners)

        if listeners and self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._fan_out_pcm, listeners, pcm_bytes)

    @staticmethod
    def _fan_out_pcm(listeners: list[asyncio.Queue[bytes]], pcm_bytes: bytes) -> None:
        for queue in listeners:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(pcm_bytes)
            except asyncio.QueueFull:
                pass

    def _emit_status(self) -> None:
        if self._status_cb and self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._status_cb)
