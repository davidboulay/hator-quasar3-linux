"""Low-level hidraw transport for the HATOR Quasar 3 family (Compx ``379a``).

Every known variant exposes its configuration channel on USB interface 1. On Linux that
interface is reachable as a ``/dev/hidrawN`` node. Thanks to the ``uaccess`` /
``plugdev`` ACLs (see ``udev/99-hator-quasar3.rules``) it is readable AND
writable without root.

This module is deliberately dependency-free (stdlib only) and protocol-agnostic:
it knows *how* to talk to the node (feature reports, output reports, input
reports), not *what* the bytes mean. Command semantics live in ``protocol.py``.
"""

from __future__ import annotations

import fcntl
import glob
import os
import re
import select

VID = 0x379A

#: Known product IDs -> (model, link). Order = connection preference: a mouse on
#: the USB cable answers instantly, dongles only answer while the RF link is up.
DEVICES: dict[int, tuple[str, str]] = {
    0x3307: ("Quasar 3 Ultra 8K", "cable"),   # mouse plugged in over USB ("Quasar 3UL")
    0x3308: ("Quasar 3 Ultra 8K", "dongle"),  # 8K 2.4 GHz receiver ("Quasar 3UL")
    0x0CF0: ("Quasar 3 Wireless", "dongle"),  # 2.4 GHz receiver ("Quasar 3WL")
}
PIDS: tuple[int, ...] = tuple(DEVICES)

#: Legacy default (original Quasar 3 Wireless dongle); prefer :data:`PIDS`.
PID = 0x0CF0

#: USB interface index that carries the vendor configuration reports.
CONFIG_INTERFACE = 1

# ---------------------------------------------------------------------------
# hidraw ioctl plumbing  (linux/hidraw.h)
# ---------------------------------------------------------------------------
_IOC_NONE, _IOC_WRITE, _IOC_READ = 0, 1, 2
_HID = ord("H")


def _ioc(direction: int, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (_HID << 8) | nr


def _hidiocgfeature(length: int) -> int:
    return _ioc(_IOC_READ | _IOC_WRITE, 0x07, length)


def _hidiocsfeature(length: int) -> int:
    return _ioc(_IOC_READ | _IOC_WRITE, 0x06, length)


_HIDIOCGRDESCSIZE = _ioc(_IOC_READ, 0x01, 4)


class DeviceNotFound(Exception):
    """Raised when the Quasar 3 dongle is not present (or wrong interface)."""


class DeviceAsleep(OSError):
    """Dongle is present but the mouse isn't answering (asleep / out of range)."""


# ---------------------------------------------------------------------------
# Device discovery via sysfs (hidraw node numbers are NOT stable across replug)
# ---------------------------------------------------------------------------
def find_interfaces(vid: int = VID, pid: int = PID) -> dict[int, str]:
    """Map ``{usb_interface_index: '/dev/hidrawN'}`` for the connected device.

    Parses ``/sys/bus/hid/devices/*/uevent`` where ``HID_ID`` looks like
    ``0003:0000379A:00000CF0`` and ``HID_PHYS`` ends in ``inputN``.
    """
    suffix = f":{vid:08X}:{pid:08X}"
    found: dict[int, str] = {}
    for uevent in glob.glob("/sys/bus/hid/devices/*/uevent"):
        try:
            fields = dict(
                line.split("=", 1)
                for line in open(uevent).read().splitlines()
                if "=" in line
            )
        except OSError:
            continue
        if not fields.get("HID_ID", "").upper().endswith(suffix):
            continue
        m = re.search(r"input(\d+)$", fields.get("HID_PHYS", ""))
        iface = int(m.group(1)) if m else -1
        hrdir = os.path.join(os.path.dirname(uevent), "hidraw")
        try:
            nodes = sorted(os.listdir(hrdir))
        except OSError:
            continue
        if nodes:
            found[iface] = os.path.join("/dev", nodes[0])
    return found


def find_devices(vid: int = VID) -> list[tuple[int, dict[int, str]]]:
    """All connected known devices as ``[(pid, {iface: node}), ...]`` in preference order."""
    out = []
    for pid in PIDS:
        ifaces = find_interfaces(vid, pid)
        if ifaces:
            out.append((pid, ifaces))
    return out


def is_connected(vid: int = VID, pid: int | None = None) -> bool:
    if pid is None:
        return bool(find_devices(vid))
    return bool(find_interfaces(vid, pid))


def describe(pid: int) -> str:
    model, link = DEVICES.get(pid, ("unknown Compx device", "?"))
    return f"HATOR {model} via {link} ({VID:04x}:{pid:04x})"


# ---------------------------------------------------------------------------
# The transport handle
# ---------------------------------------------------------------------------
class QuasarHidRaw:
    """A thin wrapper around a single ``/dev/hidrawN`` file descriptor."""

    def __init__(self, path: str, pid: int = PID):
        self.path = path
        self.pid = pid
        self.fd: int | None = None

    # -- lifecycle ----------------------------------------------------------
    def open(self) -> "QuasarHidRaw":
        if self.fd is None:
            self.fd = os.open(self.path, os.O_RDWR)
        return self

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self) -> "QuasarHidRaw":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    def _require(self) -> int:
        if self.fd is None:
            raise RuntimeError("device handle is not open")
        return self.fd

    # -- feature reports (control transfers; report_id is the first byte) ---
    def get_feature(self, report_id: int, length: int) -> bytes:
        """GET_FEATURE. ``length`` includes the report-id byte. Read-only/safe."""
        buf = bytearray(length)
        buf[0] = report_id
        n = fcntl.ioctl(self._require(), _hidiocgfeature(length), buf, True)
        return bytes(buf[: max(n, 0)])

    def set_feature(self, report_id: int, data: bytes) -> int:
        """SET_FEATURE. Sends ``[report_id] + data``. **Mutates device state.**"""
        buf = bytearray([report_id]) + bytes(data)
        return fcntl.ioctl(self._require(), _hidiocsfeature(len(buf)), buf, True)

    # -- output / input reports (interrupt endpoints) ----------------------
    def write_output(self, report_id: int, data: bytes) -> int:
        """Write an OUTPUT report ``[report_id] + data``. **Mutates device state.**"""
        return os.write(self._require(), bytes([report_id]) + bytes(data))

    def read_input(self, length: int = 64, timeout: float = 1.0) -> bytes | None:
        """Read one INPUT report, or ``None`` on timeout. Read-only/safe."""
        fd = self._require()
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            return None
        return os.read(fd, length)

    # -- descriptor --------------------------------------------------------
    def report_descriptor(self) -> bytes:
        """Read the HID report descriptor from sysfs (read-only)."""
        # /dev/hidrawN -> /sys/class/hidraw/hidrawN/device/report_descriptor
        node = os.path.basename(self.path)
        p = f"/sys/class/hidraw/{node}/device/report_descriptor"
        with open(p, "rb") as f:
            return f.read()


def open_config(
    vid: int = VID, pid: int | None = None, interface: int = CONFIG_INTERFACE
) -> QuasarHidRaw:
    """Open the vendor configuration interface; raises ``DeviceNotFound``.

    With ``pid=None`` the first connected device in :data:`PIDS` order is used
    (the high-level API in ``device.py`` goes further and picks the one that
    actually answers).
    """
    if pid is None:
        devs = find_devices(vid)
        if not devs:
            raise DeviceNotFound(
                "No HATOR Quasar 3 found — plug in the dongle or connect the USB cable"
            )
        pid, ifaces = devs[0]
    else:
        ifaces = find_interfaces(vid, pid)
        if not ifaces:
            raise DeviceNotFound(f"No device {vid:04x}:{pid:04x} found (dongle plugged in?)")
    if interface not in ifaces:
        raise DeviceNotFound(
            f"Device found but interface {interface} missing; have {sorted(ifaces)}"
        )
    return QuasarHidRaw(ifaces[interface], pid).open()
