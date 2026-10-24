# HATOR Quasar 3 — Hardware / Protocol Reconnaissance

Ground-truth notes captured directly from the connected device. Everything here is
verified on this machine (Pop!_OS 24.04), not guessed.

## Device identity

| Field | Value |
|-------|-------|
| USB VID:PID | `379a:0cf0` |
| Manufacturer | Compx (OEM chipset/platform maker) |
| Product | `Quasar 3WL` (2.4 GHz wireless receiver / dongle) |
| Connection | 2.4 GHz USB dongle (not the cable / not Bluetooth) |
| Kernel driver | `usbhid` / `hid-generic` on all interfaces |

`Compx` is the ODM behind many wireless gaming mice (Attack Shark, VGN/VXE,
Kysona, Incott, Machenike, Darmoshark, etc.). They tend to share a common HID
configuration protocol family, so sibling-brand tooling is relevant.

## Access (important)

The current user (`davidboulay`) is in the `plugdev` group and the `/dev/hidraw*`
nodes carry ACLs granting **read+write without root**:

```
/dev/hidraw4  ->  readable: yes  writable: yes
```

So the app does **not** require sudo. A udev rule may still be worth shipping to
guarantee access across reboots/replugs and on stock Ubuntu (see TODO).

## USB interfaces & hidraw mapping

The dongle exposes 3 HID interfaces. hidraw node numbers are not stable across
replug — always resolve dynamically via sysfs (`/sys/bus/hid/devices/*379A*0CF0*`).

| Interface | sysfs | hidraw (this boot) | Role |
|-----------|-------|--------------------|------|
| `:1.0` input0 | class 03/01/01 | `/dev/hidraw3` | Boot keyboard (remapped keystroke buttons) |
| `:1.1` input1 | class 03/00/00 | `/dev/hidraw4` | **Vendor config channel** |
| `:1.2` input2 | class 03/01/02 | `/dev/hidraw5` | Mouse (X/Y/wheel/5 buttons/pan) |

## Report descriptor decode — interface 1 (config channel, hidraw4)

Raw descriptor decoded into reports:

| Report ID | Page | Dir | Size (data) | Likely purpose |
|-----------|------|-----|-------------|----------------|
| `0x10` | Vendor `0xFF05` | Input | 7 B | event/notification in |
| `0x02` | Vendor `0xFF03` | Input | 7 B | event/notification in |
| `0x05` | Consumer | Input | 2 B | media keys |
| `0x03` | Generic Desktop (System) | Input | 1 B | power/sleep/wake |
| `0x08` | Vendor `0xFF02` | **Input + Output** | 16 B | **main command/response channel** |
| `0x06` | Vendor `0xFF04` | **Feature (R/W)** | 7 B | **status / quick settings** |

Transport summary:
- **Send commands:** write 17 bytes to `/dev/hidraw4` = `[0x08] + <16 payload bytes>`.
- **Read responses:** read 17-byte input reports with ID `0x08` back from the node.
- **Status:** `GET_FEATURE`/`SET_FEATURE` on report `0x06` (7 data bytes).

## Live read-only probe results

GET_FEATURE (read-only, no state change) on the config node:

```
feature 0x06 (7 data bytes) -> 00 00 64 64 64 69 08
```

- `0x64 = 100` ×3  → candidate: RGB (R,G,B) or three brightness/level values, all 100.
- `0x69 = 105`, `0x08 = 8` → unknown (mode index? brightness? speed?).

Other report IDs (`0x08`, `0x02`, `0x00`) returned all-zero echoes — i.e. not real
feature reports; `0x08` is the *output* command channel, not a feature.

Passive input read on iface1 was silent while idle (config iface only talks when
commanded). Battery/DPI state is likely retrieved by issuing a query on report `0x08`.

## What is known vs unknown

- **Known (verified):** which interface, which report IDs, report sizes, direction,
  that we can read/write without root, and one live feature snapshot.
- **Unknown (needs research or USB capture):** command *semantics* — the byte layout
  that means "set DPI = 1600", "set polling = 1000 Hz", "set RGB = ...", read battery,
  remap buttons, store macro, select onboard profile, checksum (if any).

## How to obtain command semantics

1. **Existing community decode** of the Compx `0x379a` protocol (sibling brands) —
   being researched.
2. **USB capture** of the official Windows app driving *this* device (gold standard):
   - Windows host or VM with USB passthrough of the dongle.
   - Capture with USBPcap/Wireshark while toggling each setting one at a time.
   - Filter for `SET_REPORT`/`GET_REPORT` (control) and interrupt OUT on iface1,
     report IDs `0x08`/`0x06`. Diff captures to isolate each setting's bytes.
   - Replay on Linux via hidraw writes to `/dev/hidraw4`.

## Safety notes

- No writes performed yet. Do not send arbitrary `0x08` payloads before the command
  set is understood — could corrupt onboard settings.
- First write experiments should target a single, easily-reversible setting (e.g. a
  static RGB color) and verify visually.

## Update — protocol fully decoded

The command semantics were obtained from the **official HATOR MouseHUB WebHID
build** (`controlhub.top`, gitignored under `reference/controlhub/`) and verified
byte-for-byte against this device by read-back. The full spec is in
`docs/PROTOCOL.md`. No USB capture / Windows was needed: `ReadFlashData` (opcode
8) is read-only and confirmed the offsets + checksum directly. (The earlier
feature-`0x06` snapshot `00 00 64 64 64 ...` is a separate heartbeat report, not
the flash config channel — the real config is read via report `0x08`.)

## TODO

- [x] Confirm command opcodes — done (see PROTOCOL.md).
- [x] Decide implementation stack — Python + GTK4/libadwaita.
- [ ] Ship/install the udev rule for guaranteed access on stock Ubuntu.
- [ ] Port: main backlight effects, button remap, macros, battery, profiles.

## Update 2026-10-24 — Quasar 3 Ultra 8K

The Ultra 8K is the same Compx platform with two new product IDs. Captured on
this machine with both plugged in at once (dongle in the dock, mouse on cable):

| Field | 8K dongle | Mouse on cable |
|-------|-----------|----------------|
| USB VID:PID | `379a:3308` | `379a:3307` |
| Product string | `Quasar 3UL` | `Quasar 3UL` |
| bcdDevice | `0x0215` | `0x0217` |
| Interfaces | 0 kbd / **1 config** / 2 mouse (same as 0cf0) | same |
| Report descriptor (iface 1) | as 0cf0 **plus** a mouse collection (report `0x0b`) | identical to 0cf0 |
| `GetDongleVersion (29)` | `08 1d 00 00 00 02 02 15` → 2.15 | `08 1d 01 …` (not a dongle) |
| `ReadVersionID (18)` | — (no RF link while mouse is cabled) | `08 12 00 00 00 02 02 17` → 2.17 |
| `DeviceOnLine (3)` | | `08 03 00 00 00 01 01 bb 11 d2` |

- The flash image layout, checksums, DPI/colour/polling codecs are **unchanged** —
  `ReadFlashData` returned the familiar `01 54 06 4f 01 54 00 55 …` and the six
  DPI stages decoded correctly with the PAW3950 table (`07 07 00 47` → 400 DPI).
- `ReadVersionID` reply data (`len=2`, `major, minor_bcd`) equals the USB
  `bcdDevice`, so the firmware string is `f"{b[0]:x}.{b[1]:02x}"`.
- While the mouse is on the cable, the 8K dongle enumerates but the mouse does
  not answer through it; the app therefore probes every connected ID and uses the
  first one that answers a read (cable preferred).
- Writes verified on the cabled Ultra 8K: stage-1 colour `#123456` written and
  read back, reverted to `#ffff00`; polling `8000` written (`ReportRate` code
  `0x40`) and read back, reverted to `1000`.
- Access: the 3307/3308 hidraw nodes came up `root:root 0600` until the udev rule
  was extended to those IDs (`udev/99-hator-quasar3.rules`); after
  `udevadm trigger --subsystem-match=hidraw` they are `root:plugdev 0660`.

### Lighting notes (2026-10-24, Ultra 8K)

- Out of the box the mouse was in Light mode 1 = **Rainbow** (the app had
  mis-labelled it "Static") and `DPIEffectState` was 0, i.e. the DPI indicator
  was off entirely — so neither the chosen backlight colour nor the per-stage
  colours were visible. Fixed Color = mode 3; DPI indicator Steady = mode 1 +
  state 1.
- **Writes can be silently dropped** if the next frame is sent before the
  device echoes the previous one, even with a 220 ms sleep: `DPIEffectMode`
  landed, the immediately following `DPIEffectState` write did not. The device
  echoes every `WriteFlashData` (`08 07 00 off_hi off_lo len data…`); the app
  now waits for that echo (1 s, one retry) before the next write.
