"""Command-line configurator for the HATOR Quasar 3 family (Quasar 3, Quasar 3 Ultra 8K).

Read commands are always allowed. Write commands change persistent settings and
require ``--yes``.

Examples
--------
    quasar3 info
    quasar3 show                       # decoded current settings
    quasar3 dump 0 256                 # raw flash hex
    quasar3 set polling 1000 --yes
    quasar3 set dpi 1 1600 --yes       # stage 1 -> 1600 DPI
    quasar3 set color 1 #ff0000 --yes  # stage 1 indicator -> red
    quasar3 set stage 2 --yes          # activate DPI stage 2
    quasar3 set debounce 3 --yes
    quasar3 set motionsync on --yes
    quasar3 set backlight '#ff6a00' 7 9 fixed --yes   # colour speed brightness mode
    quasar3 set dpieffect steady 5 --yes               # DPI indicator: off|steady|breathing [brightness 1-10] [speed]
"""

from __future__ import annotations

import argparse
import sys

from . import protocol as P
from .device import Quasar3
from .transport import DeviceAsleep, DeviceNotFound, describe, find_devices


def _hexbytes(s: str) -> bytes:
    return bytes.fromhex(s.replace(",", " ").replace("0x", "").replace(":", " "))


def _hx(b: bytes) -> str:
    return " ".join(f"{x:02x}" for x in b)


def _parse_mode(text: str, names: tuple[str, ...]) -> int:
    """Accept a mode index or a (prefix of a) mode name, case-insensitive."""
    if text.isdigit():
        return int(text)
    t = text.lower().replace("_", " ").replace("-", " ")
    for i, n in enumerate(names):
        if n.lower() == t or n.lower().startswith(t) or t in n.lower():
            return i
    raise SystemExit(f"unknown mode '{text}'; choose from: " +
                     ", ".join(f"{i}={n}" for i, n in enumerate(names)))


def _parse_color(parts: list[str]) -> tuple[int, int, int]:
    if len(parts) == 1 and parts[0].lstrip("#"):
        h = parts[0].lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    if len(parts) == 3:
        return tuple(int(x, 0) for x in parts)  # type: ignore
    raise SystemExit("color must be '#rrggbb' or 'r g b'")


# -- read commands ----------------------------------------------------------
def cmd_info(args) -> int:
    devs = find_devices()
    if not devs:
        print("No HATOR Quasar 3 found — plug in the dongle or connect the USB cable.")
        return 1
    for pid, ifaces in devs:
        print(f"{describe(pid)}")
        for idx in sorted(ifaces):
            print(f"  interface {idx}: {ifaces[idx]}{' (config)' if idx == 1 else ''}")
    try:
        with Quasar3.connect() as dev:
            print(f"using: {dev.describe()} on {dev.node_path()}")
            ver = dev.read_version()
            if ver:
                print(f"  firmware: {ver}")
            else:
                print("  mouse not answering (asleep? move it and retry)")
    except (DeviceNotFound, OSError) as e:
        print(f"  {e}")
    return 0


def cmd_show(args) -> int:
    with Quasar3.connect() as dev:
        print(dev.read_config().summary())
    return 0


def cmd_battery(args) -> int:
    with Quasar3.connect() as dev:
        b = dev.read_battery()
        if not b:
            print("battery: no response (mouse asleep? wiggle it and retry)")
            return 1
        print(f"battery: {b['level']}%  "
              f"{'charging' if b['charging'] else 'discharging'}  {b['voltage_mv']} mV")
    return 0


def cmd_dump(args) -> int:
    with Quasar3.connect() as dev:
        img = dev.read_flash(args.offset, args.length)
        for r in range(0, len(img), 16):
            print(f"  {args.offset + r:4d}: {_hx(img[r : r + 16])}")
    return 0


# -- write commands ---------------------------------------------------------
def _need_yes(args) -> bool:
    if not args.yes:
        print("Refusing to write without --yes (this changes persistent settings).",
              file=sys.stderr)
        return False
    return True


def cmd_set(args) -> int:
    if not _need_yes(args):
        return 2
    with Quasar3.connect() as dev:
        if not dev.wait_awake():
            print("Mouse not responding — move or click it to wake it, then retry.",
                  file=sys.stderr)
            return 1
        what = args.what
        if what == "polling":
            dev.set_polling(int(args.values[0]))
        elif what == "dpi":
            dev.set_dpi(int(args.values[0]) - 1, int(args.values[1]))
        elif what == "color":
            stage = int(args.values[0]) - 1
            dev.set_dpi_color(stage, *_parse_color(args.values[1:]))
        elif what == "stage":
            dev.set_current_dpi_stage(int(args.values[0]) - 1)
        elif what == "debounce":
            dev.set_debounce(int(args.values[0]))
        elif what == "motionsync":
            dev.set_motion_sync(args.values[0].lower() in ("on", "1", "true"))
        elif what == "sleep":
            dev.set_sleep_time(int(args.values[0]))
        elif what == "lod":
            dev.set_lod(int(args.values[0]))
        elif what == "backlight":
            cur = dev.read_config().backlight
            rgb = _parse_color([args.values[0]])
            speed = int(args.values[1]) if len(args.values) > 1 else cur["speed"]
            bright = int(args.values[2]) if len(args.values) > 2 else cur["brightness"]
            mode = _parse_mode(args.values[3], P.LIGHT_MODES) if len(args.values) > 3 else cur["mode"]
            if mode in (1, 4, 5, 6):
                print(f"note: '{P.LIGHT_MODES[mode]}' cycles the spectrum and ignores the colour; "
                      f"use 'fixed' (Fixed Color) or 'breath' (Single Color Breath) for a single colour.",
                      file=sys.stderr)
            dev.set_backlight(mode, *rgb, speed, bright)
        elif what == "dpieffect":
            mode = _parse_mode(args.values[0], P.DPI_EFFECT_MODES)
            bright = int(args.values[1]) if len(args.values) > 1 else None
            speed = int(args.values[2]) if len(args.values) > 2 else None
            dev.set_dpi_effect(mode, bright, speed)
        else:
            print(f"unknown setting '{what}'", file=sys.stderr)
            return 2
        print(f"set {what} {' '.join(args.values)} — OK")
    return 0


# -- raw experimentation ----------------------------------------------------
def cmd_feature_get(args) -> int:
    with Quasar3.connect() as dev:
        print(_hx(dev.raw_get_feature(args.id, args.len)))
    return 0


def cmd_send(args) -> int:
    if not _need_yes(args):
        return 2
    with Quasar3.connect() as dev:
        dev.raw_send(_hexbytes(args.data))
        print("sent.")
        if args.expect_reply:
            r = dev.raw_recv(1.0)
            print(f"reply: {_hx(r) if r else '(none)'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="quasar3", description="HATOR Quasar 3 / Quasar 3 Ultra 8K Linux configurator")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="detect device and show hidraw nodes").set_defaults(fn=cmd_info)
    sub.add_parser("show", help="show decoded current settings").set_defaults(fn=cmd_show)
    sub.add_parser("battery", help="read battery level (read-only)").set_defaults(fn=cmd_battery)

    sp = sub.add_parser("dump", help="hex dump flash range")
    sp.add_argument("offset", nargs="?", type=lambda x: int(x, 0), default=0)
    sp.add_argument("length", nargs="?", type=lambda x: int(x, 0), default=256)
    sp.set_defaults(fn=cmd_dump)

    sp = sub.add_parser("set", help="change a setting (needs --yes)")
    sp.add_argument("what", choices=["polling", "dpi", "color", "stage", "debounce",
                                     "motionsync", "sleep", "lod", "backlight", "dpieffect"])
    sp.add_argument("values", nargs="+")
    sp.add_argument("--yes", action="store_true")
    sp.set_defaults(fn=cmd_set)

    sp = sub.add_parser("feature-get", help="GET_FEATURE (read-only)")
    sp.add_argument("id", type=lambda x: int(x, 0))
    sp.add_argument("len", type=lambda x: int(x, 0))
    sp.set_defaults(fn=cmd_feature_get)

    sp = sub.add_parser("send", help="send raw 16-byte command (needs --yes)")
    sp.add_argument("data")
    sp.add_argument("--yes", action="store_true")
    sp.add_argument("--expect-reply", action="store_true")
    sp.set_defaults(fn=cmd_send)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except DeviceAsleep as e:
        print(f"{e}", file=sys.stderr)
        return 1
    except DeviceNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
