<!-- esphome-della-ac — local Home Assistant control for Della mini-split AC -->

<div align="center">
  <a href="https://github.com/adamgranted/esphome-della-ac">
    <picture>
      <source srcset="./.github/img/della-logo-dark.svg" media="(prefers-color-scheme: dark)">
      <img src="./.github/img/della-logo-light.svg" alt="Della" height="44"/>
    </picture>
  </a>
  <h2>esphome-della-ac</h2>
  <p align="center">
      <p><b>Local Home Assistant control for the Della 048-MS mini split</b></p>
  </p>

  <p align="center">
    <img alt="platform" src="https://img.shields.io/badge/platform-ESP8266-blue">
    <img alt="esphome" src="https://img.shields.io/badge/ESPHome-2026.5%2B-1c1c1c">
    <img alt="protocol" src="https://img.shields.io/badge/protocol-AUX%204800%208E1-orange">
  </p>

</div>


<br>

ESPHome firmware that exposes a **Della 048-MS** mini split as a full Home Assistant
thermostat — no cloud, no Tuya account. It replaces the stock Wi-Fi dongle with an
[SMLIGHT SLWF-01](https://smlight.tech/) running this firmware, plug-and-play with the
Della's USB-A service port.

The Della 048-MS does **not** speak the TCL protocol its USB port suggests — it is an
**AUX OEM** unit speaking the AUX HVAC serial protocol at **4800 baud, 8E1**, on an
open-drain line. (Sampling that line at the obvious 9600 baud yields a convincing but
bogus byte stream; that aliasing trap is why earlier attempts never worked.) The full
story and byte-level map are in [`docs/PROTOCOL.md`](docs/PROTOCOL.md).


## Features

- **Full climate entity** — off / cool / heat / dry / fan-only / heat_cool, fan
  auto / low / medium / high / quiet, vertical swing, boost (turbo) and sleep presets,
  current temperature and HVAC action
- **Telemetry sensors** — evaporator-coil, compressor and outdoor temperatures, plus
  inverter power %
- **Robust RX** — receives over a pulse-capture path that is immune to the AC MCU's
  ragged rising edges, then reconstructs and CRC-checks every frame on-device
- **Safe writes** — each command is built by copying the unit's latest status frame and
  changing only the requested fields, and is refused if the last readback is stale
- **1 Hz state** — sub-2-second updates in Home Assistant; bus load stays light
- **Local only** — native ESPHome API + OTA; nothing leaves your network


## Hardware

- A **Della 048-MS** mini split (other AUX-built Della / AUX-OEM units likely work — see
  [`docs/PROTOCOL.md`](docs/PROTOCOL.md)).
- A **SMLIGHT SLWF-01** (ESP-12F). It drops straight into the indoor unit's USB-A service
  port and already carries everything the link needs — the USB-A header, 5 V regulation,
  and the level shifting between the ESP8266's 3.3 V logic and the AC's 5 V TTL UART
  (AC-side UART on **GPIO12 = TX, GPIO14 = RX**). No wiring, no extra parts.
- **DIY or other ESP8266 boards** are likely supported, but you would have to provide that
  same supporting circuitry yourself — there is no reference design here. The SLWF-01 is
  the simple path.
- The service port is a 4-pin 5 V TTL UART, **not** a USB device — do not plug it into a
  computer.


## Installation

### Option 1 — flash the release build (no toolchain)

A pre-built, secret-free image is published with each [release](https://github.com/adamgranted/esphome-della-ac/releases).
Plug the SLWF-01 into your computer over USB and **[install it from your browser](https://adamgranted.github.io/esphome-della-ac/)**
(Chrome/Edge), or download `della-ac-esp8266.factory.bin` and flash it with
`esptool.py write_flash 0x0 della-ac-esp8266.factory.bin`.

On first boot the dongle has no Wi-Fi, so it starts an **`AC-wifi`** hotspot
(password `slwf01pro`). Join it, enter your network in the captive portal, then
move the dongle to the AC's service port and adopt it in Home Assistant — set
your own API key and OTA password when you do.

### Option 2 — build from source

1. Install [ESPHome](https://esphome.io/) (2026.5.3 known-good).
2. Copy the secrets template and fill it in: `cp secrets.yaml.example secrets.yaml`.
3. Flash (USB the first time, OTA thereafter): `esphome run della-slwf.yaml`.
4. Accept the device the **ESPHome** integration auto-discovers in Home Assistant;
   the `Della AC` climate entity and telemetry sensors appear on its device page.

Both builds share [`della-ac.base.yaml`](della-ac.base.yaml) — `della-slwf.yaml`
adds your secrets, `della-ac.factory.yaml` is the secret-free release image.


## How it works

Receive is handled by an ESPHome `remote_receiver` capturing raw pin timings, which a
lambda reconstructs into 4800-baud 8E1 bytes and CRC-validates. A tiny local component
(`components/della_ac/`) maps those bytes onto a Home Assistant climate entity and builds
outgoing commands. Keeping the protocol logic in the YAML lambda and only the
HA-thermostat shell in C++ is deliberate — see
[`docs/PROTOCOL.md`](docs/PROTOCOL.md) for the framing, field maps, CRC, and the quirks
the firmware compensates for.


## Repository layout

| Path | What |
|------|------|
| `della-ac.base.yaml` | Shared firmware body (RX decode, polling, climate + telemetry entities) |
| `della-slwf.yaml` | Personal build — base + your secrets (`esphome run` this) |
| `della-ac.factory.yaml` | Secret-free release image — base + `AC-wifi` AP for pairing |
| `components/della_ac/` | Local ESPHome climate component (the HA-thermostat shell) |
| `della-la.yaml` | Bare "logic analyzer" build — raw pulse dump for protocol work |
| `analyze_bursts.py`, `pulse2bytes.py` | Offline decoders: log pulses → bytes → CRC |
| `della_ctl.py` | Native-API remote control (list / press / set entities) |
| `test_ladder.py` | Scripted control-verification ladder over the native API |
| `ir_sweep.py` | Guided capture: press a remote button, see the decoded field change |
| `docs/PROTOCOL.md` | The AUX protocol: discovery, frames, field maps, quirks |


## Credits

- [GrKoR/AUX_HVAC_Protocol](https://github.com/GrKoR/AUX_HVAC_Protocol) and
  [GrKoR/esphome_aux_ac_component](https://github.com/GrKoR/esphome_aux_ac_component) —
  the AUX protocol documentation and reference implementation this work builds on.
- [dudanov/iot-uni-dongle](https://github.com/dudanov/iot-uni-dongle) — dongle hardware
  reference.
