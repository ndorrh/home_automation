"""Windows keep-awake power management via SetThreadExecutionState."""

from __future__ import annotations

import ctypes
import logging
import sys

logger = logging.getLogger("smart_home_av.power")

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


class PowerManager:
    """Prevents Windows system sleep while allowing the monitor to sleep."""

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled and sys.platform == "win32"
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active

    def enable_keep_awake(self) -> bool:
        """Request Windows to keep the system awake in the background."""
        if not self._enabled:
            return False
        try:
            res = ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED
            )
            self._active = res != 0
            if self._active:
                logger.info("Windows keep-awake enabled (monitor can still sleep).")
            return self._active
        except Exception as exc:
            logger.warning("Could not set thread execution state: %s", exc)
            return False

    def restore_normal_sleep(self) -> None:
        """Clear keep-awake flag on clean shutdown."""
        if not self._active or sys.platform != "win32":
            return
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            self._active = False
            logger.info("Restored default Windows sleep policy.")
        except Exception as exc:
            logger.warning("Failed to restore sleep policy: %s", exc)
