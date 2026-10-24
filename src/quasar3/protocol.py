"""Command codecs for the HATOR Quasar 3 (Compx ``379a:0cf0``).

Ported byte-for-byte from the official "HATOR MouseHUB" WebHID build
(controlhub.top). See ``docs/PROTOCOL.md`` for the full annotated derivation.

Model
-----
The mouse stores all settings in a flat flash image (mirror array ``Ne`` in the
web app). The host reads/writes ranges of that image with two opcodes:

    * ``ReadFlashData``  (8): read ``len`` bytes at a big-endian offset.
    * ``WriteFlashData`` (7): write up to 10 bytes at a big-endian offset.

Every command is a 16-byte payload sent as HID **report id 8** (so 17 bytes on
the wire: ``[0x08] + 16``). Settings are just well-known offsets into the image
(``OFF``); encoders translate human values (Hz, DPI, RGB) to/from raw bytes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- transport-level constants (verified on hardware + official JS) --------
REPORT_ID = 8          # HID report id for the command channel (JS: va=8)
FRAME_LEN = 16         # payload bytes after the report id
MAX_CHUNK = 10         # max data bytes per flash read/write frame

# Command opcodes (JS: er) — byte[0] of the payload.
CMD = {
    "EncryptionData": 1, "PCDriverStatus": 2, "DeviceOnLine": 3, "BatteryLevel": 4,
    "DongleEnterPair": 5, "GetPairState": 6, "WriteFlashData": 7, "ReadFlashData": 8,
    "ClearSetting": 9, "StatusChanged": 10, "GetCurrentConfig": 14, "SetCurrentConfig": 15,
    "ReadVersionID": 18, "Set4KDongleRGB": 20, "Get4KDongleRGBValue": 21,
    "SetLongRangeMode": 22, "GetLongRangeMode": 23, "SetDongleRGBBarMode": 24,
    "GetDongleRGBBarMode": 25, "GetDongleVersion": 29,
}

# Flash-image offsets (JS: ut). Sizes noted where known.
OFF = {
    "ReportRate": 0,          # 1 byte value (+complement) — polling rate code
    "maxDpiStage": 2,         # 1 byte — number of active DPI stages
    "CurrentDPI": 4,          # 1 byte — active DPI stage index
    "LOD": 10,                # 1 byte — lift-off distance
    "DPIValue": 12,           # 4 bytes/stage — encoded DPI (X/Y + high bits + cksum)
    "DPIColor": 44,           # 4 bytes/stage — [R,G,B,cksum]
    "DPIEffectMode": 76,
    "DPIEffectBrightness": 78,
    "DPIEffectSpeed": 80,
    "DPIEffectState": 82,
    "KeyFunction": 96,        # 4 bytes/button
    "Light": 160,             # 7 bytes — main backlight [mode,R,G,B,speed,brightness,cksum]
    "LightState": 167,        # 1 byte (0/1) — main backlight on/off (JS writes offset 167 directly)
    "DebounceTime": 169,      # 1 byte (ms)
    "MotionSync": 171,        # 1 byte (0/1)
    "SleepTime": 173,         # 1 byte (minutes)
    "Angle": 175,
    "Ripple": 177,
    "MovingOffLight": 179,
    "PerformanceState": 181,
    "Performance": 183,
    "SensorMode": 185,
    "AngleTune": 189,
    "AngleTuneState": 191,
    "ShortcutKey": 256,
    "Macro": 768,
}

CHECKSUM_BASE = 85  # 0x55 (JS: Wr uses 85 - sum)


# --- checksums (JS: function Wr) -------------------------------------------
def _wr_signed(buf) -> int:
    """JS ``Wr``: ``85 - (sum(buf[:-1]) & 0xFF)`` (may be negative, like JS)."""
    return CHECKSUM_BASE - (sum(buf[:-1]) & 0xFF)


def frame_checksum(frame16) -> int:
    """Checksum byte for a 16-byte command frame: ``Wr(frame) - REPORT_ID``."""
    return (_wr_signed(frame16) - REPORT_ID) & 0xFF


def data_checksum(data) -> int:
    """Embedded data checksum (per-color / per-DPI-word): ``Wr(data)``."""
    return _wr_signed(data) & 0xFF


# --- frame builders --------------------------------------------------------
def build_read(offset: int, length: int) -> bytes:
    """16-byte ReadFlashData frame (JS: fg/ea)."""
    f = bytearray(FRAME_LEN)
    f[0] = CMD["ReadFlashData"]
    f[2] = (offset >> 8) & 0xFF
    f[3] = offset & 0xFF
    f[4] = length & 0xFF
    f[15] = frame_checksum(f)
    return bytes(f)


def build_write_byte(offset: int, value: int) -> bytes:
    """16-byte WriteFlashData frame for one logical byte (JS: vr).

    Payload carries ``value`` and its complement ``85-value`` (length field 2).
    """
    f = bytearray(FRAME_LEN)
    f[0] = CMD["WriteFlashData"]
    f[2] = (offset >> 8) & 0xFF
    f[3] = offset & 0xFF
    f[4] = 2
    f[5] = value & 0xFF
    f[6] = (CHECKSUM_BASE - value) & 0xFF
    f[15] = frame_checksum(f)
    return bytes(f)


def build_write_chunk(offset: int, data: bytes) -> bytes:
    """16-byte WriteFlashData frame for up to 10 raw bytes (JS: jr inner loop)."""
    if len(data) > MAX_CHUNK:
        raise ValueError("chunk > 10 bytes; use write_flash() to split")
    f = bytearray(FRAME_LEN)
    f[0] = CMD["WriteFlashData"]
    f[2] = (offset >> 8) & 0xFF
    f[3] = offset & 0xFF
    f[4] = len(data) & 0xFF
    for i, b in enumerate(data):
        f[5 + i] = b & 0xFF
    f[15] = frame_checksum(f)
    return bytes(f)


def iter_write_chunks(offset: int, data: bytes):
    """Yield (chunk_offset, frame) splitting ``data`` into 10-byte writes."""
    for i in range(0, len(data), MAX_CHUNK):
        chunk = data[i : i + MAX_CHUNK]
        yield offset + i, build_write_chunk(offset + i, chunk)


def iter_read_chunks(offset: int, length: int):
    """Yield (chunk_offset, chunk_len, frame) splitting a read into 10-byte reads."""
    end = offset + length
    a = offset
    while a < end:
        s = min(MAX_CHUNK, end - a)
        yield a, s, build_read(a, s)
        a += s


@dataclass
class FlashResponse:
    opcode: int
    offset: int
    length: int
    data: bytes


def parse_response(report: bytes) -> FlashResponse | None:
    """Parse an input report (incl. leading report-id byte) from the device.

    Layout (after stripping report id): ``[opcode, ?, off_hi, off_lo, len, data...]``.
    Returns ``None`` if it is not a flash response (e.g. a battery/status event).
    """
    if not report or report[0] != REPORT_ID or len(report) < 6:
        return None
    dt = report[1:]
    opcode = dt[0]
    offset = (dt[2] << 8) | dt[3]
    length = dt[4]
    data = bytes(dt[5 : 5 + length])
    return FlashResponse(opcode, offset, length, data)


# --- value codecs ----------------------------------------------------------
def encode_polling(hz: int) -> int:
    """Hz -> flash code (JS setter $Z)."""
    return (1000 // hz) if hz <= 1000 else (hz // 2000 * 16)


def decode_polling(code: int) -> int:
    """Flash code -> Hz (JS decoder Aj)."""
    if code == 0:
        return 0
    return (code // 16 * 2000) if code >= 16 else (1000 // code)


def encode_color(r: int, g: int, b: int) -> bytes:
    """RGB -> 4-byte DPIColor word ``[R,G,B,checksum]`` (JS: WZ)."""
    word = bytes([r & 0xFF, g & 0xFF, b & 0xFF, 0])
    return word[:3] + bytes([data_checksum(word)])


def decode_color(word: bytes) -> tuple[int, int, int]:
    return (word[0], word[1], word[2])


# --- DPI codec (JS: wN/GZ) -------------------------------------------------
# Sensor DPI ranges from controlhub.top/HATOR/sensor.json. Each range:
# (min, max, step, dpiEx). Index 3 is special-cased (n=4) in the firmware;
# indexes 1/2 use n=2; index 0 uses n=1. step comes from range[0].
SENSORS = {
    "3950": [(50, 30000, 50, 0), (30100, 60000, 100, 17)],   # PAW3950 (Quasar 3 Ultra)
    "3395": [(50, 26000, 50, 0)],
    "3311": [(50, 12000, 50, 0)],
    "3104": [(50, 8000, 50, 0)],
}
DEFAULT_SENSOR = "3950"


def _sensor_ranges(sensor: str):
    return SENSORS.get(sensor, SENSORS[DEFAULT_SENSOR])


def encode_dpi(dpi: int, sensor: str = DEFAULT_SENSOR) -> bytes:
    """DPI value -> 4-byte DPIValue word ``[Xlow, Ylow, high|dpiEx, checksum]``."""
    ranges = _sensor_ranges(sensor)
    step = ranges[0][2]
    a = len(ranges) - 1
    while a >= 0 and not (dpi >= ranges[a][0]):
        a -= 1
    a = max(a, 0)
    if a == 3:
        n, dpiex = 4, 51
    elif a in (1, 2):
        n, dpiex = 2, ranges[a][3]
    else:
        n, dpiex = 1, 0
    val = (dpi // n) // step - 1
    o = (val >> 8) & 0xFF
    word = bytes([val & 0xFF, val & 0xFF, ((o << 2) | (o << 6) | dpiex) & 0xFF, 0])
    return word[:3] + bytes([data_checksum(word)])


def decode_dpi(word: bytes, sensor: str = DEFAULT_SENSOR) -> int:
    """4-byte DPIValue word -> DPI (inverse of :func:`encode_dpi`)."""
    ranges = _sensor_ranges(sensor)
    step = ranges[0][2]
    low = word[0]
    high = (word[2] >> 2) & 0x03
    val = low | (high << 8)
    n = 2 if (word[2] & 0x10) else 1
    return (val + 1) * step * n


# --- backlight / Light zone (JS: x_) ---------------------------------------
# Light block @ offset 160, 7 bytes: [mode, R, G, B, speed, brightness, checksum],
# followed by LightState @ 167 (1 = on). Speed/brightness are 0..9.
def encode_light(mode: int, r: int, g: int, b: int, speed: int, brightness: int) -> bytes:
    e = bytes([mode & 0xFF, r & 0xFF, g & 0xFF, b & 0xFF, speed & 0xFF, brightness & 0xFF, 0])
    return e[:6] + bytes([data_checksum(e)])


def decode_light(b8: bytes) -> dict:
    """Decode 8 bytes from offset 160 (7-byte Light block + LightState)."""
    return {
        "mode": b8[0],
        "rgb": (b8[1], b8[2], b8[3]),
        "speed": min(b8[4], 9),
        "brightness": min(b8[5], 9),
        "on": (b8[7] == 1) if len(b8) > 7 else True,
    }


# Light effect modes — exact values/names from the official app's
# ``mouse.light.LightModeOptions`` (controlhub.top/HATOR/lang/en.json).
# NOTE: the colour only applies to modes 2 (Single Color Breath) and 3 (Fixed
# Color); 1/4/5/6 cycle through the spectrum and ignore it.
LIGHT_MODES = ("Off", "Rainbow", "Single Color Breath", "Fixed Color", "Neon",
               "Rainbow Breath", "Fixed Rainbow")
LIGHT_MODES_USING_COLOR = (2, 3)


# --- DPI indicator effect (JS: YZ/XZ/qZ/jZ, offsets 76..82) ----------------
# ``mouse.light.DPIEffectOptions``: 0 Off, 1 Steady, 2 Breathing. "Off" is
# stored as DPIEffectState=0 (mode byte untouched); choosing a mode re-arms
# DPIEffectState=1. Brightness is a 1..10 UI level mapped through SN()/xN().
DPI_EFFECT_MODES = ("Off", "Steady", "Breathing")


def encode_dpi_effect_brightness(level: int) -> int:
    """UI level 1..10 -> raw byte (JS ``SN``)."""
    if level == 1:
        return 16
    if level in (2, 3, 4, 6, 7, 8):
        return 30 * (level - 1)
    if level == 5:
        return 128
    if level == 9:
        return 230
    if level == 10:
        return 255
    return 128


def decode_dpi_effect_brightness(raw: int) -> int:
    """Raw byte -> UI level 1..10 (JS ``xN``)."""
    if raw % 30 == 0:
        return raw // 30 + 1
    return {16: 1, 128: 5, 230: 9, 255: 10}.get(raw, 5)


def decode_dpi_effect(img: bytes) -> dict:
    """Decode the DPI effect fields from a flash image starting at offset 0."""
    return {
        "mode": img[OFF["DPIEffectMode"]],
        "brightness": decode_dpi_effect_brightness(img[OFF["DPIEffectBrightness"]]),
        "speed": img[OFF["DPIEffectSpeed"]],
        "on": img[OFF["DPIEffectState"]] == 1,
    }


# --- generic no-arg / small-arg command (JS: Ls/jd) ------------------------
def build_command(opcode: int, args: bytes = b"") -> bytes:
    f = bytearray(FRAME_LEN)
    f[0] = opcode
    f[4] = len(args) & 0xFF
    for i, a in enumerate(args):
        f[5 + i] = a & 0xFF
    f[15] = frame_checksum(f)
    return bytes(f)


def parse_battery(report: bytes) -> dict | None:
    """Parse a BatteryLevel (opcode 4) input report (incl. report-id byte).

    JS: ``level=Dt[5], charging=Dt[6]==1, voltage=(Dt[7]<<8)+Dt[8]``.
    """
    if not report or report[0] != REPORT_ID or len(report) < 10:
        return None
    if report[1] != CMD["BatteryLevel"]:
        return None
    return {
        "level": report[6],
        "charging": report[7] == 1,
        "voltage_mv": (report[8] << 8) | report[9],
    }


# Standard polling options actually selectable; the device/firmware caps these.
POLLING_RATES_HZ = (125, 250, 500, 1000, 2000, 4000, 8000)
