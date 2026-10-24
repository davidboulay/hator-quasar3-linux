"""High-level device API for the HATOR Quasar 3.

Flash reads are implemented and safe (they never modify the device). Flash
writes mutate persistent settings; they replicate the official app byte-for-byte
but are still gated by callers (the CLI requires ``--yes``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from . import protocol as P
from .transport import (
    CONFIG_INTERFACE,
    DEVICES,
    DeviceAsleep,
    DeviceNotFound,
    QuasarHidRaw,
    describe,
    find_devices,
    find_interfaces,
    open_config,
)

# The official app waits ~200 ms between commands; the platform can hang if
# pushed faster. We use a smaller settle for reads and the full delay for writes.
READ_SETTLE = 0.05
WRITE_SETTLE = 0.22


@dataclass
class Config:
    """Decoded view of the mouse's flash settings (offsets 0..255)."""

    raw: bytes
    polling_hz: int
    max_dpi_stage: int
    current_dpi_stage: int
    debounce_ms: int
    motion_sync: int
    sleep_time: int
    lod: int
    dpi_values: list[int]
    dpi_colors: list[tuple[int, int, int]]
    dpi_raw: list[bytes]
    backlight: dict  # {mode, rgb, speed, brightness, on}
    dpi_effect: dict  # {mode, brightness(1-10), speed, on}

    def summary(self) -> str:
        lines = [
            f"polling rate   : {self.polling_hz} Hz",
            f"DPI stages     : {self.max_dpi_stage} (active: stage {self.current_dpi_stage + 1})",
            f"debounce       : {self.debounce_ms} ms",
            f"motion sync    : {'on' if self.motion_sync else 'off'}",
            f"sleep timer    : {self.sleep_time} min",
            f"lift-off dist  : {self.lod}",
            f"backlight      : {P.LIGHT_MODES[self.backlight['mode']] if self.backlight['mode'] < len(P.LIGHT_MODES) else 'mode ' + str(self.backlight['mode'])}"
            f"{'' if self.backlight['on'] else ' (switched off)'}  "
            f"#{self.backlight['rgb'][0]:02x}{self.backlight['rgb'][1]:02x}{self.backlight['rgb'][2]:02x}"
            f"  speed {self.backlight['speed']}  brightness {self.backlight['brightness']}",
            f"DPI indicator  : {P.DPI_EFFECT_MODES[self.dpi_effect['mode']] if self.dpi_effect['mode'] < len(P.DPI_EFFECT_MODES) else 'mode ' + str(self.dpi_effect['mode'])}"
            f"{'' if self.dpi_effect['on'] else ' (switched off)'}  "
            f"brightness {self.dpi_effect['brightness']}/10  speed {self.dpi_effect['speed']}",
            "DPI stages:",
        ]
        for i, (r, g, b) in enumerate(self.dpi_colors):
            mark = " *" if i == self.current_dpi_stage else "  "
            lines.append(
                f" {mark}stage {i + 1}: {self.dpi_values[i]:>6} DPI  "
                f"#{r:02x}{g:02x}{b:02x}  raw={self.dpi_raw[i].hex()}"
            )
        return "\n".join(lines)


class Quasar3:
    """A connected HATOR Quasar 3 family mouse (via its 2.4 GHz dongle or the USB cable)."""

    def __init__(self, handle: QuasarHidRaw, sensor: str = P.DEFAULT_SENSOR):
        self._h = handle
        self.sensor = sensor  # DPI encoding is sensor-specific (Quasar 3 Ultra = PAW3950)
        self.pid = handle.pid
        self.model, self.link = DEVICES.get(self.pid, ("Quasar 3", "?"))

    # -- connection --------------------------------------------------------
    @classmethod
    def connect(cls, pid: int | None = None) -> "Quasar3":
        """Open the mouse.

        With ``pid=None`` every connected known device is tried in preference
        order (cable first, then dongles) and the first one whose mouse answers
        a read is used. If nothing answers (mouse asleep / out of range) the
        first present device is returned so callers can still report it.
        """
        if pid is not None:
            return cls(open_config(pid=pid))
        devs = find_devices()
        if not devs:
            raise DeviceNotFound(
                "No HATOR Quasar 3 found — plug in the dongle or connect the USB cable"
            )
        first: "Quasar3 | None" = None
        for cand_pid, ifaces in devs:
            if CONFIG_INTERFACE not in ifaces:
                continue
            try:
                dev = cls(QuasarHidRaw(ifaces[CONFIG_INTERFACE], cand_pid).open())
            except OSError:
                continue  # e.g. permission denied on this node; try the next one
            if len(devs) == 1 or dev.ping(retries=2):
                if first is not None:
                    first.close()
                return dev
            if first is None:
                first = dev
            else:
                dev.close()
        if first is None:
            raise DeviceNotFound(
                "HATOR Quasar 3 present but its config interface is not accessible "
                "(install udev/99-hator-quasar3.rules)"
            )
        return first

    @staticmethod
    def is_present() -> bool:
        return bool(find_devices())

    def describe(self) -> str:
        return describe(self.pid)

    def close(self) -> None:
        self._h.close()

    def __enter__(self) -> "Quasar3":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def node_path(self) -> str:
        return self._h.path

    # -- flash read (safe) -------------------------------------------------
    def _read_chunk(self, choff: int, clen: int, frame: bytes, timeout: float,
                    retries: int) -> bytes | None:
        for _ in range(retries):
            self._h.write_output(P.REPORT_ID, frame)
            deadline = time.time() + timeout
            while time.time() < deadline:
                rep = self._h.read_input(P.FRAME_LEN + 1, timeout=timeout)
                if rep is None:
                    break
                resp = P.parse_response(rep)
                if resp and resp.opcode == P.CMD["ReadFlashData"] and resp.offset == choff:
                    return resp.data[:clen]
            time.sleep(READ_SETTLE)
        return None

    def ping(self, retries: int = 4) -> bool:
        """Quick liveness check: is the mouse awake and answering? (read-only)"""
        _, clen, frame = next(iter(P.iter_read_chunks(0, 2)))
        return self._read_chunk(0, clen, frame, timeout=0.4, retries=retries) is not None

    def wait_awake(self, timeout: float = 8.0) -> bool:
        """Poll until the 2.4 GHz link wakes (it naps between commands) or time out."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.ping(retries=2):
                return True
        return False

    def read_flash(self, offset: int, length: int, *, settle: float = READ_SETTLE,
                   timeout: float = 0.6, retries: int = 3) -> bytes:
        """Read ``length`` bytes from the flash image starting at ``offset``.

        Raises :class:`DeviceAsleep` if the mouse stops answering (asleep / range).
        """
        out = bytearray()
        for choff, clen, frame in P.iter_read_chunks(offset, length):
            got = self._read_chunk(choff, clen, frame, timeout, retries)
            if got is None or len(got) < clen:
                raise DeviceAsleep(
                    f"mouse not responding at offset {choff} — move or click it to wake it"
                )
            out += got
            time.sleep(settle)
        return bytes(out)

    def read_config(self) -> Config:
        """Read and decode the standard settings block (offsets 0..255)."""
        if not self.wait_awake():
            raise DeviceAsleep("mouse not responding — move or click it to wake it, then retry")
        img = self.read_flash(0, 256)

        def b(name: str) -> int:
            return img[P.OFF[name]]

        n_stages = b("maxDpiStage")
        values, colors, dpi_raw = [], [], []
        for s in range(max(n_stages, 1)):
            base_c = P.OFF["DPIColor"] + s * 4
            base_d = P.OFF["DPIValue"] + s * 4
            word_d = bytes(img[base_d : base_d + 4])
            values.append(P.decode_dpi(word_d, self.sensor))
            colors.append(P.decode_color(img[base_c : base_c + 4]))
            dpi_raw.append(word_d)
        return Config(
            raw=img,
            polling_hz=P.decode_polling(b("ReportRate")),
            max_dpi_stage=n_stages,
            current_dpi_stage=b("CurrentDPI"),
            debounce_ms=b("DebounceTime"),
            motion_sync=b("MotionSync"),
            sleep_time=b("SleepTime"),
            lod=b("LOD"),
            dpi_values=values,
            dpi_colors=colors,
            dpi_raw=dpi_raw,
            backlight=P.decode_light(bytes(img[P.OFF["Light"] : P.OFF["Light"] + 8])),
            dpi_effect=P.decode_dpi_effect(img),
        )

    def read_version(self, timeout: float = 1.5) -> str | None:
        """Firmware version of the mouse (``ReadVersionID``, read-only), e.g. ``"2.17"``.

        Reply layout: ``[8, 0x12, 0, 0, 0, len=2, major, minor_bcd]`` — the two
        data bytes equal the USB ``bcdDevice`` (``0x0217`` -> ``2.17``).
        """
        self._h.write_output(P.REPORT_ID, P.build_command(P.CMD["ReadVersionID"]))
        deadline = time.time() + timeout
        while time.time() < deadline:
            rep = self._h.read_input(P.FRAME_LEN + 1, timeout=0.4)
            if rep is None:
                continue
            if rep[0] == P.REPORT_ID and rep[1] == P.CMD["ReadVersionID"] and len(rep) >= 8:
                time.sleep(READ_SETTLE)
                return f"{rep[6]:x}.{rep[7]:02x}"
        return None

    def read_battery(self, timeout: float = 3.0) -> dict | None:
        """Request and parse battery status (read-only). Returns {level, charging, voltage_mv}.

        The dongle polls the mouse over RF, so the reply can take 1-2 s; we loop
        and drain unrelated input reports until it arrives or we time out.
        """
        self._h.write_output(P.REPORT_ID, P.build_command(P.CMD["BatteryLevel"]))
        deadline = time.time() + timeout
        while time.time() < deadline:
            rep = self._h.read_input(P.FRAME_LEN + 1, timeout=0.4)
            if rep is None:
                continue
            batt = P.parse_battery(rep)
            if batt is not None:
                time.sleep(READ_SETTLE)
                return batt
        return None

    # -- flash write (mutates persistent settings) -------------------------
    def _write_frame(self, offset: int, frame: bytes, *, timeout: float = 1.0,
                     retries: int = 2) -> None:
        """Send one WriteFlashData frame and wait for the device to echo it back.

        The official app awaits the reply before the next command; just sleeping
        is not enough — back-to-back writes can be silently dropped (seen on the
        Ultra 8K: DPIEffectMode landed, the following DPIEffectState write did not).
        """
        for attempt in range(retries):
            self._h.write_output(P.REPORT_ID, frame)
            deadline = time.time() + timeout
            while time.time() < deadline:
                rep = self._h.read_input(P.FRAME_LEN + 1, timeout=timeout)
                if rep is None:
                    break
                resp = P.parse_response(rep)
                if resp and resp.opcode == P.CMD["WriteFlashData"] and resp.offset == offset:
                    time.sleep(WRITE_SETTLE)
                    return
            time.sleep(WRITE_SETTLE)
        raise DeviceAsleep(f"write at offset {offset} not acknowledged — mouse asleep?")

    def write_byte(self, offset: int, value: int) -> None:
        self._write_frame(offset, P.build_write_byte(offset, value))

    def write_flash(self, offset: int, data: bytes) -> None:
        for choff, frame in P.iter_write_chunks(offset, data):
            self._write_frame(choff, frame)

    # high-level setters (verified-layout; still write to persistent flash)
    def set_polling(self, rate_hz: int) -> None:
        self.write_byte(P.OFF["ReportRate"], P.encode_polling(rate_hz))

    def set_debounce(self, ms: int) -> None:
        self.write_byte(P.OFF["DebounceTime"], ms)

    def set_motion_sync(self, on: bool) -> None:
        self.write_byte(P.OFF["MotionSync"], 1 if on else 0)

    def set_current_dpi_stage(self, stage: int) -> None:
        self.write_byte(P.OFF["CurrentDPI"], stage)

    def set_dpi_color(self, stage: int, r: int, g: int, b: int) -> None:
        self.write_flash(P.OFF["DPIColor"] + stage * 4, P.encode_color(r, g, b))

    def set_dpi(self, stage: int, dpi: int) -> None:
        self.write_flash(P.OFF["DPIValue"] + stage * 4, P.encode_dpi(dpi, self.sensor))

    def set_sleep_time(self, minutes: int) -> None:
        self.write_byte(P.OFF["SleepTime"], minutes)

    def set_lod(self, value: int) -> None:
        self.write_byte(P.OFF["LOD"], value)

    def set_backlight(self, mode: int, r: int, g: int, b: int, speed: int, brightness: int) -> None:
        """Mirror of the official ``Set_MS_LightMode``/``x_``: mode 0 ("Off") only
        clears LightState; any other mode re-arms LightState and writes the block."""
        if mode == 0:
            self.write_byte(P.OFF["LightState"], 0)
            return
        self.write_byte(P.OFF["LightState"], 1)
        self.write_flash(P.OFF["Light"], P.encode_light(mode, r, g, b, speed, brightness))

    def set_dpi_effect(self, mode: int, brightness: int | None = None,
                       speed: int | None = None) -> None:
        """DPI indicator effect (official ``Set_MS_DPILightMode/Off/Brightness/Speed``).

        ``mode`` 0 = Off (clears DPIEffectState), 1 = Steady, 2 = Breathing.
        ``brightness`` is the 1..10 UI level; ``speed`` 1..10.
        """
        if mode == 0:
            self.write_byte(P.OFF["DPIEffectState"], 0)
        else:
            self.write_byte(P.OFF["DPIEffectMode"], mode)
            self.write_byte(P.OFF["DPIEffectState"], 1)
        if brightness is not None:
            self.write_byte(P.OFF["DPIEffectBrightness"], P.encode_dpi_effect_brightness(brightness))
        if speed is not None:
            self.write_byte(P.OFF["DPIEffectSpeed"], speed)

    # -- low-level escape hatches (for reverse-engineering) ----------------
    def raw_send(self, frame: bytes) -> int:
        return self._h.write_output(P.REPORT_ID, (bytes(frame) + bytes(P.FRAME_LEN))[: P.FRAME_LEN])

    def raw_recv(self, timeout: float = 1.0) -> bytes | None:
        return self._h.read_input(P.FRAME_LEN + 1, timeout=timeout)

    def raw_get_feature(self, report_id: int, length: int) -> bytes:
        return self._h.get_feature(report_id, length)


__all__ = ["Quasar3", "Config", "DeviceNotFound"]
