# Della 048-MS mini split ⇄ ESPHome (SMLIGHT SLWF-01)

Full local Home Assistant control of a **Della 048-MS** mini split by replacing the stock
Tuya WBR3 USB dongle with a **SMLIGHT SLWF-01** (ESP8266) running ESPHome. Exposes a
complete HA thermostat (modes, fan, swing, presets, action) plus coil/compressor/outdoor
temperature and inverter-power telemetry — all decoded locally, no cloud.

> **TL;DR protocol finding:** the Della 048-MS is an **AUX OEM** unit. Its dongle port
> speaks the **AUX HVAC serial protocol at 4800 baud 8E1** on an open-drain line — *not*
> the TCL 9600-baud protocol its TCL-style USB port suggests. If you sample the line at
> 9600 baud you get a convincing-but-bogus `9E E6 06 00 00 00 00 00 1E 3E FE` "frame"
> (aliasing). Frame docs: [GrKoR/AUX_HVAC_Protocol](https://github.com/GrKoR/AUX_HVAC_Protocol).

## Hardware

- **AC:** Della 048-MS (brand: Align Inc; hardware: AUX). Stock dongle: Tuya WBR3
  (RTL8720CF) bridging Tuya cloud ⇄ AUX serial.
- **Dongle:** SMLIGHT SLWF-01 r2.x — ESP-12F, FET level shifters with 10 kΩ pull-ups,
  AC-side UART on **GPIO12 (TX) / GPIO14 (RX)**, powered from the AC's USB-A port (5 V).
- The bus is open-drain, idles HIGH. Falling edges are crisp; **rising edges lag 0–155 µs**
  (the AC MCU bit-bangs TX on a ~51.5 µs tick and releases late). This matters: a naive
  UART receiver at 4800 can mis-sample the first 1-bit after a long 0-run. This firmware
  therefore receives via `remote_receiver` pulse capture and reconstructs bytes in
  software (lows are timer-exact; highs get +77 µs recentring) — decode error rate in
  practice ≈ 0.1%, every frame CRC-checked.

## Protocol (verified live against the unit)

- **Framing:** `BB 00 <type> <dir> .. .. <body_len> 00 [body] [CRC16]`.
  CRC16 = RFC1071-style ones-complement word sum over header+body, big-endian on the
  wire, odd length padded with a trailing zero byte.
- **Ping** (unit → module, ~2.5 s when idle): `BB 00 01 00 00 00 00 00 43 FF`.
  Module presence answer: `BB 00 01 80 01 00 08 00 1C 27 00 00 00 00 00 00 1E 58`.
  The unit stops pinging entirely while actively polled — normal.
- **Status requests** (module → unit):
  small `BB 00 06 80 00 00 02 00 11 01 2B 7E`, big `BB 00 06 80 00 00 02 00 21 01 1B 7E`.
  The unit also auto-reports big status every ~10 min and ~10 s after dongle boot.
- **Small status** (settings; 15-byte body, packet byte = body index + 8):
  `[2]` v-louver (bits 0-2: 0=swing, 7=stop) | setpoint −8 << 3 ·
  `[4]` minutes-since-IR (bits 0-5) | half-degree flag (bit 7) ·
  `[5]` fan (0xE0: 0x20 HIGH, 0x40 MED, 0x60 LOW, 0xA0 AUTO) ·
  `[6]` turbo 0x40 | mute 0x80 ·
  `[7]` mode (0xE0: 0x00 auto, 0x20 cool, 0x40 dry, 0x80 heat, 0xC0 fan) | °F display 0x02 | sleep 0x04 ·
  `[10]` power 0x20 | clean 0x04 | health 0x02 ·
  `[12]` display 0x10 | mildew 0x08 · `[13]` inverter power limit · `[14]` setpoint tenths
- **Big status** (telemetry; 24-byte body, cmd `0x20` unsolicited / `0x21` polled):
  packet byte 15 indoor T (−0x20, tenths at byte 31), 16-18 evaporator coil, 20 outdoor,
  22 compressor, 24 inverter power %. CONF byte 10: 0xE4 unsolicited / 0xE0 poll reply.
- **Control** (SET_PARAMS): `BB 00 06 80 00 00 0F 00` + 15-byte body + CRC. Body layout =
  small status body with `[0]=0x01 [1]=0x01`. **Safety pattern: copy the unit's latest
  small-status body verbatim and modify only the fields you mean to change.** The unit
  ACKs with a 4-byte-body type-0x07 frame echoing your CRC.

### Quirks (all verified live)

- **AUTO (heat_cool) mode force-resets the setpoint to 25.0 °C** — and it persists after
  switching back to cool. Re-send the setpoint after any auto-mode excursion.
- **Setpoint tenths:** values ending .0/.5 store exactly; anything else stores 0.1 °C
  LOW (sent 22.8 → stored 22.7) — **on every accepted SET, including ones that don't
  change temperature**. Since SET bodies are copies of the settings frame, an innocent
  fan/swing/preset command erodes a non-.0/.5 setpoint by 0.1 °C per command (caught
  in the wild via an HA swing command). The component compensates: it always re-encodes
  the intended target with tenths+1 for non-.0/.5 values, so stored == intended and
  non-temperature commands are setpoint-neutral. The half-degree flag (body[4] bit 7)
  is set only when the *sent* tenths are exactly 5.
- Polling at 1 Hz is fully sustainable (measured: 59/59 replies over 120 s, ~19% bus
  duty). Frames can arrive ~11 ms apart — keep receiver idle threshold under that.

## Files

- `della-slwf.yaml` — main firmware: pulse-decoder RX, length-aware frame splitter,
  CRC validation, polling scheduler, ping-ACK, climate + telemetry entities.
- `components/della_ac/` — minimal local climate component (the HA thermostat shell;
  control = copy-body-modify-field, refuses on stale readback).
- `della-la.yaml` — bare "logic analyzer" build (remote_receiver raw dump only); flash
  this for pristine pulse-timing research.
- `analyze_bursts.py` / `pulse2bytes.py` — offline tools: parse `remote.raw` log dumps
  (incl. chunked lines), classify bursts, decode pulses → 8E1 bytes → CRC check.
- `della_ctl.py` — ESPHome native-API remote control (list/press/number/watch).
  `test_ladder.py` — incremental control-verification ladder (24 checks).
  Both honor `DELLA_HOST` (default `della-slwf.local`) and read `secrets.yaml` from CWD.
- `ir_sweep.py` — guided interactive IR-remote capture: per-button prompts, live
  before/after diff of the settings frame with decoded fields, loud flagging of
  never-seen bits, free-form section for leftover buttons, JSON+MD session reports.
- `captures/` — sanitized log captures documenting the discovery path (the 9600-baud
  aliasing era → first raw pulse capture). Network identifiers masked.

## Usage

```bash
pip install esphome            # 2026.5.3 known-good
cp secrets.yaml.example secrets.yaml   # fill in
esphome run della-slwf.yaml    # flash (first time via USB, then OTA)
python3 della_ctl.py list      # poke entities over the native API
```

HA discovers the device via the ESPHome integration; the climate entity exposes
off / cool / heat / dry / fan-only / heat_cool, fan auto/low/med/high/quiet,
vertical swing, presets boost (turbo) & sleep, current temperature and action.

## Credits

- [GrKoR/AUX_HVAC_Protocol](https://github.com/GrKoR/AUX_HVAC_Protocol) and
  [GrKoR/esphome_aux_ac_component](https://github.com/GrKoR/esphome_aux_ac_component) —
  the AUX protocol documentation and reference implementation this work was verified against.
- [dudanov/iot-uni-dongle](https://github.com/dudanov/iot-uni-dongle) — dongle hardware
  reference (level-shifter topology).
