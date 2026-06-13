# Della AC — ESPHome firmware

Local Home Assistant control for the **Della 048-MS** mini split — no cloud, no
Tuya account. Runs on a [SMLIGHT SLWF-01](https://smlight.tech/) dongle in the
indoor unit's USB-A service port. Source and docs:
[github.com/adamgranted/esphome-della-ac](https://github.com/adamgranted/esphome-della-ac).

## Install

Plug the SLWF-01 into your computer over USB, then click **Install** below
(Chrome or Edge — the browser talks to the board directly; nothing is uploaded
anywhere).

<esp-web-install-button manifest="firmware/della-ac.manifest.json"></esp-web-install-button>

After it flashes, the dongle starts a Wi-Fi hotspot named **`AC-wifi`**
(password `slwf01pro`). Join it, pick your network in the captive portal, then
plug the dongle into the AC's service port and adopt it in Home Assistant.

<script type="module" src="https://unpkg.com/esp-web-tools@10/dist/web/install-button.js?module"></script>
