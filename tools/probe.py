#!/usr/bin/env python3
"""Read-only reconnaissance probe for the HATOR Quasar 3 config interface.

Performs ONLY safe operations: device discovery, report-descriptor dump,
GET_FEATURE reads, and passive INPUT reads. It never writes to the device, so
it cannot change any settings. Use it to observe live state and to confirm the
node is reachable before/while developing the protocol layer.

    python3 tools/probe.py
    python3 tools/probe.py --watch 5     # passively watch input reports for 5s
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Allow running straight from the repo without installation.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quasar3 import transport as T  # noqa: E402


def hx(b: bytes) -> str:
    return " ".join(f"{x:02x}" for x in b)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--watch", type=float, default=0.0,
                    help="seconds to passively watch input reports (move/click the mouse)")
    args = ap.parse_args()

    ifaces = T.find_interfaces()
    if not ifaces:
        print(f"Device {T.VID:04x}:{T.PID:04x} not found. Is the dongle plugged in?")
        return 1

    print("Interfaces -> hidraw nodes:")
    for idx in sorted(ifaces):
        print(f"  iface {idx}: {ifaces[idx]}")
    print()

    dev = T.open_config()
    try:
        print(f"Config node: {dev.path}")
        try:
            rd = dev.report_descriptor()
            print(f"Report descriptor: {len(rd)} bytes")
        except OSError as e:
            print(f"Report descriptor: unavailable ({e})")
        print()

        print("GET_FEATURE probes (read-only):")
        for rid in (0x06, 0x08, 0x10, 0x02, 0x05, 0x04, 0x09, 0x0A, 0x07, 0x01):
            for length in (8, 17, 33, 65):
                try:
                    r = dev.get_feature(rid, length)
                except OSError:
                    continue
                if r:
                    print(f"  id=0x{rid:02x} len={length:<2} -> {hx(r)}")
                    break
        print()

        if args.watch > 0:
            print(f"Watching input reports for {args.watch:g}s — move/click the mouse...")
            t0 = time.time()
            seen: dict[int, bytes] = {}
            while time.time() - t0 < args.watch:
                r = dev.read_input(64, timeout=0.2)
                if r:
                    seen.setdefault(r[0], r)
            if seen:
                for k in sorted(seen):
                    print(f"  input id=0x{k:02x} -> {hx(seen[k])}")
            else:
                print("  (no input reports on config iface — it stays quiet until commanded)")
    finally:
        dev.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
