# Della 048-MS — AUX HVAC serial protocol

Everything here was verified live against a Della 048-MS. If you have a different Della
or AUX-OEM model, treat the field offsets as a strong starting point, not gospel.

## TL;DR

- The unit is an **AUX OEM** (brand owner Align Inc; stock dongle a Tuya WBR3 bridging
  Tuya cloud ⇄ AUX serial).
- The USB-A dongle port is a 4-pin **5 V TTL UART**, **4800 baud, 8E1**, on an open-drain
  line that idles HIGH.
- It is **not** the TCL 9600-baud protocol the TCL-style port suggests. Sampling at
  9600 baud aliases the stream into a believable-but-wrong frame
  (`9E E6 06 00 00 00 00 00 1E 3E FE`) — the trap earlier work fell into.
- Frame docs upstream: [GrKoR/AUX_HVAC_Protocol](https://github.com/GrKoR/AUX_HVAC_Protocol).

## Why pulse capture instead of a UART peripheral

Falling edges on this bus are crisp, but the **rising edges lag 0–155 µs** — the AC MCU
bit-bangs its TX on a ~51.5 µs tick and releases the line late, and the pull-up's RC
recovery softens it further. A normal UART receiver sampling at mid-bit can misread the
first `1` after a long run of `0`s. So this firmware receives with `remote_receiver`
(raw pin timings) and reconstructs bytes in a lambda: LOW pulses are timer-exact, and
HIGH runs get a +77 µs recentring before being rounded to bit counts. Measured decode
error rate in practice ≈ 0.1 %, and every frame is CRC-checked, so bad frames are simply
dropped.

The `della-la.yaml` build is a stripped-down logic analyzer (raw dump only) for
capturing pristine timings; `analyze_bursts.py` and `pulse2bytes.py` decode those logs
offline.

## Framing

```
BB 00 <type> <dir> .. .. <body_len> 00 [body ...] [CRC16_hi CRC16_lo]
```

- **CRC16** = RFC1071 one's-complement word sum over header+body, big-endian on the
  wire; an odd length is padded with a trailing zero byte for the sum.
- Frames can arrive as little as ~11 ms apart, so the receiver's idle gap must be set
  below that and the parser must be able to split multiple frames out of one burst.

### Ping (presence)

- Unit → module, every ~2.5 s while idle: `BB 00 01 00 00 00 00 00 43 FF`
- Module presence answer: `BB 00 01 80 01 00 08 00 1C 27 00 00 00 00 00 00 1E 58`
- The unit stops pinging entirely while it is being actively polled — that is normal;
  the poll replies prove the link.

### Status requests (module → unit)

- Small (settings): `BB 00 06 80 00 00 02 00 11 01 2B 7E`
- Big (telemetry):  `BB 00 06 80 00 00 02 00 21 01 1B 7E`
- The unit also auto-reports big status every ~10 min and ~10 s after dongle boot.

## Small status — settings (15-byte body)

Packet byte index = body index + 8.

| Byte | Field | Notes |
|------|-------|-------|
| 2 | v-louver (bits 0–2) \| setpoint int (bits 3–7) | louver 0 = swing, 7 = stop; temp = `8 + (v >> 3)` °C |
| 3 | h-louver (bits 5–7) | 0 = swing, `0x20` = stop |
| 4 | minutes-since-last-command (bits 0–5) \| half-degree flag (bit 7) | counter resets on any accepted command |
| 5 | fan speed (bits 5–7) | `0x20` high, `0x40` med, `0x60` low, `0xA0` auto |
| 6 | turbo (bit 6) \| mute (bit 7) | |
| 7 | mode (bits 5–7) \| °F display (bit 1) \| sleep (bit 2) | mode `0x00` auto, `0x20` cool, `0x40` dry, `0x80` heat, `0xC0` fan |
| 10 | power (bit 5) \| eco (bit 3) \| iClean (bit 2) \| health/ion (bits 0–1) | health sets both low bits (`0x03`); iClean is honoured only with power off (reports `0x04`, power bit clear) |
| 12 | display (bit 4) \| anti-fungus (bit 3) | anti-fungus is set only with the unit off; arms a post-shutdown dry cycle |
| 13 | inverter power limit (bit 7 enable, bits 0–6 value) | |
| 14 | setpoint tenths | combined with byte 2 integer part |

## Big status — telemetry (24-byte body)

`cmd 0x20` when unsolicited, `0x21` when polled. Packet byte indices:

| Byte | Field | Encoding |
|------|-------|----------|
| 13 | reported fan speed | live blower speed: `2` low, `4` med, `6` high (in auto, reports the speed the unit picked) |
| 15 | indoor temperature | `value − 0x20` °C, tenths in byte 31 low nibble |
| 16–18 | evaporator coil temperature | `value − 0x20` °C |
| 20 | outdoor temperature | `value − 0x20` °C |
| 22 | compressor temperature | `value − 0x20` °C |
| 24 | inverter power | percent |

(CONF byte 10: `0xE4` unsolicited / `0xE0` poll reply.)

## Control (SET_PARAMS)

```
BB 00 06 80 00 00 0F 00  [15-byte body]  [CRC16]
```

The body is the **small-status body with `[0]=0x01 [1]=0x01`**. The safe construction
this firmware uses: take the unit's most recent small-status body verbatim and change
only the fields the command touches. The unit ACKs with a short type-`0x07` frame
echoing your CRC.

## Quirks (handled by the firmware)

- **AUTO (heat_cool) force-resets the setpoint to 25 °C**, and it persists after
  switching back to another mode. Re-send the setpoint after any auto-mode excursion.
- **Setpoint tenths erode by 0.1 °C on every accepted SET** whose tenths are not `.0` or
  `.5` — and because SET bodies are copies of the settings frame, even a fan- or
  swing-only command will drift a `.x` setpoint downward over repeated commands. The
  firmware compensates by re-encoding the intended target with tenths + 1 for non-`.0`/
  `.5` values (so the unit stores exactly what you meant), and only sets the half-degree
  flag when the *sent* tenths are `5`. A consequence: stored tenths of `.4`/`.9` are not
  reachable — irrelevant when the display is in °F.
- **`minutes-since-last-command`** (byte 4 low bits) resets on serial commands too, not
  just the IR remote — it is really "minutes since the last command from any source."
- Polling at 1 Hz is sustainable: measured 59/59 replies over 120 s at roughly 19 % bus
  duty.

## Tooling

| Tool | Use |
|------|-----|
| `della-la.yaml` | Flash to capture raw pulse timings (`esphome logs della-la.yaml`) |
| `analyze_bursts.py <log>` | Parse a raw-dump log, classify bursts, report timing variance |
| `pulse2bytes.py <log>` | Decode pulses → 8E1 bytes → CRC check |
| `della_ctl.py` | Poke entities over the native API |
| `test_ladder.py` | Drive every control path and verify against the unit's readback |
| `ir_sweep.py` | Press a remote button, watch the decoded settings-field diff |

Tools that talk to the device read `secrets.yaml` from the working directory and take the
host from `$DELLA_HOST` (default `della-slwf.local`).
