"""quasar3 — Linux configuration tool for the HATOR Quasar 3 family (Compx 379a)."""

__version__ = "0.1.1"

from .transport import (  # noqa: F401
    VID,
    PID,
    PIDS,
    DEVICES,
    CONFIG_INTERFACE,
    DeviceAsleep,
    DeviceNotFound,
    QuasarHidRaw,
    describe,
    find_devices,
    find_interfaces,
    is_connected,
    open_config,
)
