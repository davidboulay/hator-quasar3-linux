# HATOR Quasar 3 — Configuration Protocol

Reverse-engineered from the official **HATOR MouseHUB** WebHID build
(`controlhub.top/HATOR/`, bundle `assets/index-*.js`) and **verified byte-for-byte
against the live devices** (`379a:0cf0` Quasar 3 Wireless dongle; `379a:3307`
Quasar 3 Ultra 8K on cable) via read-back. The Ultra 8K dongle is `379a:3308`.
This is the spec the `quasar3` app implements.

## Transport

- All config travels on **USB interface 1** (vendor usage page `0xFF05`), i.e.
  `/dev/hidraw4` on Linux — readable/writable without root (see `RECON.md`).
- Every command is **HID report id `0x08`** + a **16-byte payload** (17 bytes on
  the wire). Responses arrive as input reports with the same report id.
- The firmware needs **≥200 ms between commands** or the channel can hang. The
  app uses `WRITE_SETTLE = 0.22 s`.

### Frame layout (payload, after the report-id byte)

```
byte 0   opcode            (7 = WriteFlashData, 8 = ReadFlashData, …)
byte 1   0
byte 2   offset >> 8       big-endian flash offset
byte 3   offset & 0xFF
byte 4   length            data length (writes: bytes at 5+, reads: bytes to read)
byte 5.. data              up to 10 bytes
byte 15  frame checksum
```

### Checksum (JS `Wr`)

```
wr(buf)        = 85 - (sum(buf[0..len-2]) & 0xFF)     # 85 = 0x55, may be negative
frame[15]      = (wr(frame) - 8) & 0xFF               # 8 = report id
data_checksum  =  wr(data)      & 0xFF                # embedded in DPI/colour words
```

Two checksum levels exist: the **frame** checksum (byte 15, minus the report id)
and **embedded data** checksums inside multi-byte words (DPI value, colour).

## Opcodes (`er`)

`EncryptionData 1, PCDriverStatus 2, DeviceOnLine 3, BatteryLevel 4,
DongleEnterPair 5, GetPairState 6, WriteFlashData 7, ReadFlashData 8,
ClearSetting 9, StatusChanged 10, GetCurrentConfig 14, SetCurrentConfig 15,
ReadVersionID 18, Set4KDongleRGB 20, Get4KDongleRGBValue 21, SetLongRangeMode 22,
GetLongRangeMode 23, SetDongleRGBBarMode 24, GetDongleRGBBarMode 25,
GetDongleVersion 29`

> ⚠️ Destructive — do not issue: `ClearSetting 9`, `SetDeviceVidPid 11`,
> bootloader/update opcodes.

## Settings are flash offsets (`ut`)

All settings are bytes in a flat flash image, read with `ReadFlashData` and
written with `WriteFlashData`. Multi-byte writes are chunked into ≤10-byte frames.

| Offset | Name | Size | Encoding |
|-------:|------|------|----------|
| 0 | ReportRate | 1 (+complement) | polling code (below) |
| 2 | maxDpiStage | 1 | number of active DPI stages |
| 4 | CurrentDPI | 1 | active stage index (0-based) |
| 10 | LOD | 1 | lift-off distance |
| 12 | DPIValue | 4/stage | DPI word (below) |
| 44 | DPIColor | 4/stage | `[R, G, B, data_checksum]` |
| 76 | DPIEffectMode | 1 | |
| 78 | DPIEffectBrightness | 1 | `xN()`-mapped |
| 80 | DPIEffectSpeed | 1 | |
| 82 | DPIEffectState | 1 | |
| 96 | KeyFunction | 4/button | `[type, code_hi, code_lo, cksum]` (WIP) |
| 160 | Light | 7 | main backlight `[mode,R,G,B,speed,brightness,cksum]` (modes below) |
| 167 | LightState | 1 | main backlight on/off — "Off" in the app writes this, not the mode |
| 169 | DebounceTime | 1 | ms |
| 171 | MotionSync | 1 | 0/1 |
| 173 | SleepTime | 1 | minutes |
| 175 | Angle | 1 | angle snapping |
| 177 | Ripple | 1 | |
| 256 | ShortcutKey | — | (WIP) |
| 768 | Macro | — | (WIP) |

### Polling rate

```
encode: hz <= 1000 ? 1000 // hz : hz // 2000 * 16
decode: code >= 16 ? code // 16 * 2000 : 1000 // code
# 125->8  250->4  500->2  1000->1  2000->16  4000->32  8000->64
```

ReportRate stores `[code, 85-code]`; only the wireless dongle's caps apply
(Quasar 3 Wireless = 1000 Hz; Ultra/8K models go higher with the 8K dongle).

### DPI value (sensor-specific; `wN`/`GZ`)

Sensor ranges come from `controlhub.top/HATOR/sensor.json`. For **PAW3950**
(Quasar 3 Ultra): `[{min 50, max 30000, step 50, DPIex 0}, {min 30100, max 60000,
step 100, DPIex 17}]`.

```
range index a = highest range whose .min <= dpi
n, dpiEx = (4,51) if a==3; (2, range[a].DPIex) if a in (1,2); else (1,0)
val = (dpi // n) // step - 1                          # step = range[0].step
word = [ val & 0xFF,                                   # X low
         val & 0xFF,                                   # Y low (X=Y)
         ((val>>8)<<2) | ((val>>8)<<6) | dpiEx,         # X/Y high bits + dpiEx
         data_checksum ]
decode: low=word[0]; high=(word[2]>>2)&3; val=low|(high<<8); n=2 if word[2]&0x10 else 1
        dpi = (val + 1) * step * n
```

Verified: word `09 09 00 43` → `(9+1)*50 = 500 DPI`.

### DPI indicator colour (`WZ`)

```
word = [R, G, B, data_checksum]           # data_checksum = (85 - (R+G+B)) & 0xFF
offset = DPIColor(44) + stage * 4
```

Verified: `ff ff 00` → checksum `0x57`, read back identical.

## Read responses (`ReadFlashData`)

Input report (after stripping report id): `[8, _, off_hi, off_lo, len, data…]`.
Reads are chunked into ≤10-byte requests (`fg`) with the same 200 ms spacing.

## Lighting modes (from `lang/en.json`, verified 2026-10-24)

`Light[0]` (`mouse.light.LightModeOptions`):

| Value | Name | Uses the colour bytes? |
|------:|------|------------------------|
| 0 | Off | — (app clears `LightState`=167 instead) |
| 1 | Rainbow | no — spectrum cycle |
| 2 | Single Color Breath | yes |
| 3 | Fixed Color | yes |
| 4 | Neon | no |
| 5 | Rainbow Breath | no |
| 6 | Fixed Rainbow | no |

So a mouse that "stays rainbow" after setting a colour is simply in mode 1;
write mode 3 to get a static colour. Speed/brightness are 0..9.

DPI indicator (`mouse.light.DPIEffectOptions`): `DPIEffectMode` 0 Off, 1 Steady,
2 Breathing. "Off" is stored as `DPIEffectState`(82)=0 with the mode byte left
alone; selecting a mode writes the mode and re-arms `DPIEffectState`=1.
`DPIEffectBrightness`(78) is a raw byte mapped from a 1..10 level by `SN()`
(`1→16, 2/3/4/6/7/8→30·(n−1), 5→128, 9→230, 10→255`), decoded by `xN()`.

## Version (`ReadVersionID 18`, `GetDongleVersion 29`)

Reply: `[8, opcode, 0, 0, 0, len=2, major, minor_bcd]`; the two data bytes equal
the USB `bcdDevice` (`02 17` → firmware `2.17`). `GetDongleVersion` answers only
on a dongle; a cabled mouse replies `08 1d 01 00 …`.

## Status / battery (partially ported)

`BatteryLevel (4)` and the `StatusChanged (10)` async events report battery and
state changes; `GetCurrentConfig/SetCurrentConfig (14/15)` manage onboard
profiles. These are the next features to add.

## Reference

The original (gitignored) JS/JSON live under `reference/controlhub/`. Functions
referenced above: `Wr` (checksum), `vr` (write byte), `jr` (write multi),
`ea`/`fg` (read), `wN`/`GZ` (DPI), `WZ` (colour), `$Z`/`Aj` (polling),
maps `er` (opcodes), `ut` (offsets).
