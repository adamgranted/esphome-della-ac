<div align="center">
  <img src="img/della-logo.svg" alt="Della" width="200"><br><br>
  <p><strong>Local Home Assistant control for the Della 048-MS mini split.</strong><br>
  No cloud, no Tuya account — runs on a <a href="https://smlight.tech/">SMLIGHT SLWF-01</a> dongle.</p>
  <p><a href="https://github.com/adamgranted/esphome-della-ac">View the project on GitHub →</a></p>
</div>

---

## Flash it from your browser

Connect the SLWF-01 to your computer over USB-C and click **Connect** (Chrome or
Edge). The browser talks to the board directly — nothing is uploaded anywhere.

<p align="center">
  <esp-web-install-button manifest="firmware/della-ac.manifest.json"></esp-web-install-button>
</p>

## After it flashes

You can set your Wi-Fi two ways:

- **In the browser** — once installed, the flasher offers a **Configure Wi-Fi**
  step over the same USB-C connection.
- **Or via the hotspot** — the dongle starts an **`AC-wifi`** network
  (password `slwf01pro`); join it and pick your Wi-Fi in the captive portal.

Then move the dongle to the AC's USB-A service port and adopt it in Home
Assistant. Set your own API key and OTA password when you do — the released
image ships with neither baked in.

Prefer to build from source? See the
[README](https://github.com/adamgranted/esphome-della-ac#installation).

<script type="module" src="https://unpkg.com/esp-web-tools@10/dist/web/install-button.js?module"></script>
