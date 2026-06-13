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
      <p><b>Local Home Assistant control for Della mini-split air conditioners</b></p>
  </p>

  <p align="center">
    <img alt="platform" src="https://img.shields.io/badge/platform-ESP8266-blue">
    <img alt="esphome" src="https://img.shields.io/badge/ESPHome-2026.5%2B-1c1c1c">
    <img alt="protocol" src="https://img.shields.io/badge/protocol-AUX%204800%208E1-orange">
  </p>

</div>


<br>

ESPHome firmware that exposes a **Della 048-MS** mini split as a full Home Assistant
thermostat — no cloud, no Tuya account. It replaces the stock Wi-Fi dongle with any
ESP8266 stick (developed on a [SMLIGHT SLWF-01](https://smlight.tech/)) plugged into the
indoor unit's USB-A service port.

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

- A **Della 048-MS** mini split (other AUX-built Della/AUX-OEM units may work — see
  [`docs/PROTOCOL.md`](docs/PROTOCOL.md)).
- An **ESP8266** dongle for the indoor unit's USB-A port. Developed on the SMLIGHT
  SLWF-01 (ESP-12F; AC-side UART on **GPIO12 = TX, GPIO14 = RX**, 5 V from the port).
- The port is a standard 4-pin 5 V TTL UART. It is **not** a USB device — do not plug it
  into a computer.


## Installation

1. Install [ESPHome](https://esphome.io/) (2026.5.3 known-good).
2. Copy the secrets template and fill it in:
   ```bash
   cp secrets.yaml.example secrets.yaml
   ```
3. Flash the firmware (USB the first time, OTA thereafter):
   ```bash
   esphome run della-slwf.yaml
   ```
4. In Home Assistant, accept the device the **ESPHome** integration auto-discovers. The
   `Della AC` climate entity and telemetry sensors appear on the device page.


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
| `della-slwf.yaml` | Main firmware (RX decode, polling, climate + telemetry entities) |
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
