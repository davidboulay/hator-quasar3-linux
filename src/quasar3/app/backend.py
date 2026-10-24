"""GUI-facing backend: wraps the device with capability flags and a change-apply API.

All device I/O here is blocking; the GUI calls these from a worker thread and
marshals results back with ``GLib.idle_add``.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import protocol as P
from ..device import Config, Quasar3
from ..transport import DeviceAsleep, DeviceNotFound, find_devices

# Confirmed against hardware (read+write verified). Flip on as features land.
CAPS = {
    "dpi": True,        # per-stage DPI value + active stage
    "polling": True,    # report rate
    "rgb": True,        # per-stage indicator colors
    "tuning": True,     # debounce / motion sync / sleep / LOD
    "backlight": True,  # main Light zone: mode/color/speed/brightness
    "buttons": False,   # button remap — layout known, encoding WIP
    "macros": False,
}


@dataclass
class Overview:
    connected: bool
    node: str | None = None
    error: str | None = None


class Backend:
    @staticmethod
    def cap(name: str) -> bool:
        return CAPS.get(name, False)

    @staticmethod
    def present() -> bool:
        """Is a dongle or the cabled mouse plugged in (regardless of whether it is awake)?"""
        return bool(find_devices())

    def overview(self) -> Overview:
        if not find_devices():
            return Overview(connected=False)
        try:
            with Quasar3.connect() as dev:
                return Overview(connected=True, node=dev.node_path())
        except (DeviceNotFound, OSError) as e:
            return Overview(connected=False, error=str(e))

    def load(self) -> Config | None:
        """Read and decode current settings, or None if unavailable."""
        try:
            with Quasar3.connect() as dev:
                return dev.read_config()
        except (DeviceNotFound, OSError):
            return None

    def battery(self) -> dict | None:
        try:
            with Quasar3.connect() as dev:
                return dev.read_battery()
        except (DeviceNotFound, OSError):
            return None

    def apply(self, changes: dict) -> list[str]:
        """Apply a dict of changes; return a list of human-readable applied lines.

        Supported keys: polling(int), stage(int 0-based), debounce(int),
        motionsync(bool), sleep(int), lod(int), dpi({stage:dpi}), color({stage:(r,g,b)}).
        """
        applied: list[str] = []
        with Quasar3.connect() as dev:
            if not dev.wait_awake():
                raise DeviceAsleep("mouse not responding — move or click it to wake it")
            if "polling" in changes:
                dev.set_polling(changes["polling"]); applied.append(f"polling {changes['polling']} Hz")
            for stage, dpi in changes.get("dpi", {}).items():
                dev.set_dpi(stage, dpi); applied.append(f"stage {stage + 1} DPI {dpi}")
            for stage, (r, g, b) in changes.get("color", {}).items():
                dev.set_dpi_color(stage, r, g, b); applied.append(f"stage {stage + 1} color #{r:02x}{g:02x}{b:02x}")
            if "stage" in changes:
                dev.set_current_dpi_stage(changes["stage"]); applied.append(f"active stage {changes['stage'] + 1}")
            if "debounce" in changes:
                dev.set_debounce(changes["debounce"]); applied.append(f"debounce {changes['debounce']} ms")
            if "motionsync" in changes:
                dev.set_motion_sync(changes["motionsync"]); applied.append(f"motion sync {'on' if changes['motionsync'] else 'off'}")
            if "sleep" in changes:
                dev.set_sleep_time(changes["sleep"]); applied.append(f"sleep {changes['sleep']} min")
            if "lod" in changes:
                dev.set_lod(changes["lod"]); applied.append(f"LOD {changes['lod']}")
            if "backlight" in changes:
                m, r, g, b, sp, br = changes["backlight"]
                dev.set_backlight(m, r, g, b, sp, br)
                applied.append(f"backlight {P.LIGHT_MODES[m] if m < len(P.LIGHT_MODES) else m} #{r:02x}{g:02x}{b:02x}")
            if "dpieffect" in changes:
                m, br, sp = changes["dpieffect"]
                dev.set_dpi_effect(m, br, sp)
                applied.append(f"DPI indicator {P.DPI_EFFECT_MODES[m] if m < len(P.DPI_EFFECT_MODES) else m}")
        return applied
