// della_ac — minimal AUX-protocol climate for the Della 048-MS.
// RX is handled OUTSIDE this class (remote_receiver pulse decode in YAML, which
// is immune to the AC MCU's rising-edge release jitter); decoded small/big
// status frames are fed in via ingest_small()/ingest_big(). TX goes through
// the uart device (GPIO12 -> USB D+ -> AC RX, open-drain via FET shifter).
//
// Control safety: every SET_PARAMS body starts as a verbatim copy of the
// unit's most recent small-status body, then ONLY the fields named in the
// ClimateCall are modified. Refused if the last readback is stale (>15 s;
// at the 1 s poll cadence it is ~2 s old at most).
//
// Protocol facts (verified live 2026-06-12, see HANDOFF.md):
//   small body: [2] vlouver(0-2)|setpoint-8<<3   [4] ir-min(0-5)|frac.5(7)
//               [5] fan(0xE0: 20=HI 40=MED 60=LO A0=AUTO)
//               [6] turbo(0x40)|mute(0x80)
//               [7] mode(0xE0: 00=auto 20=cool 40=dry 80=heat C0=fan)
//                   |degF(0x02)|sleep(0x04)
//               [10] power(0x20)  [12] display(0x10)  [14] setpoint tenths
//   frac flag rule: set only when tenths == 5 (else unit lands 0.1 low)
//   CRC16: RFC1071 word sum over header+body, hi byte first, odd pads zero.
#pragma once

#include "esphome/core/component.h"
#include "esphome/core/log.h"
#include "esphome/components/climate/climate.h"
#include "esphome/components/uart/uart.h"

namespace esphome {
namespace della_ac {

static const char *const TAG = "della_ac";

class DellaAC : public climate::Climate, public Component, public uart::UARTDevice {
 public:
  void setup() override {}
  void dump_config() override { ESP_LOGCONFIG(TAG, "Della AC (AUX protocol, RX via pulse decoder)"); }

  // ---- state ingestion (called from the YAML on_raw frame dispatcher) ----

  void ingest_small(const uint8_t *body, size_t len) {
    if (len < 15)
      return;
    memcpy(this->small_body_, body, 15);
    this->have_small_ = true;
    this->last_small_ms_ = millis();

    bool power = body[10] & 0x20;
    uint8_t md = body[7] & 0xE0;
    climate::ClimateMode mode;
    if (!power) {
      mode = climate::CLIMATE_MODE_OFF;
    } else {
      switch (md) {
        case 0x20: mode = climate::CLIMATE_MODE_COOL; break;
        case 0x40: mode = climate::CLIMATE_MODE_DRY; break;
        case 0x80: mode = climate::CLIMATE_MODE_HEAT; break;
        case 0xC0: mode = climate::CLIMATE_MODE_FAN_ONLY; break;
        default:   mode = climate::CLIMATE_MODE_HEAT_COOL; break;  // AUX "auto"
      }
    }

    float target = 8.0f + ((body[2] >> 3) & 0x1F) + body[14] / 10.0f;

    climate::ClimateFanMode fan;
    if (body[6] & 0x80) {
      fan = climate::CLIMATE_FAN_QUIET;  // mute flag
    } else {
      switch (body[5] & 0xE0) {
        case 0x20: fan = climate::CLIMATE_FAN_HIGH; break;
        case 0x40: fan = climate::CLIMATE_FAN_MEDIUM; break;
        case 0x60: fan = climate::CLIMATE_FAN_LOW; break;
        default:   fan = climate::CLIMATE_FAN_AUTO; break;
      }
    }

    climate::ClimateSwingMode swing =
        ((body[2] & 0x07) == 0x00) ? climate::CLIMATE_SWING_VERTICAL : climate::CLIMATE_SWING_OFF;

    climate::ClimatePreset preset = climate::CLIMATE_PRESET_NONE;
    if (body[6] & 0x40)
      preset = climate::CLIMATE_PRESET_BOOST;       // turbo
    else if (body[7] & 0x04)
      preset = climate::CLIMATE_PRESET_SLEEP;

    bool changed = mode != this->mode || fan != this->fan_mode || swing != this->swing_mode ||
                   preset != this->preset || fabsf(target - this->target_temperature) > 0.05f;
    this->mode = mode;
    this->target_temperature = target;
    this->fan_mode = fan;
    this->swing_mode = swing;
    this->preset = preset;
    this->update_action_();
    if (changed || this->first_publish_) {
      this->first_publish_ = false;
      this->publish_state();
    }
  }

  void ingest_big(float indoor, uint8_t inverter_power) {
    this->inverter_power_ = inverter_power;
    bool changed = isnan(this->current_temperature) ||
                   fabsf(indoor - this->current_temperature) > 0.05f;
    this->current_temperature = indoor;
    changed = this->update_action_() || changed;
    if (changed)
      this->publish_state();
  }

  // display on/off (small body[12] bit 4) — for a YAML template switch
  void set_display(bool on) {
    if (!this->fresh_()) {
      ESP_LOGW(TAG, "display: no fresh small status, refusing");
      return;
    }
    uint8_t b[15];
    memcpy(b, this->small_body_, 15);
    b[0] = 0x01; b[1] = 0x01;
    b[4] &= 0x80;
    if (on) b[12] |= 0x10; else b[12] &= ~0x10;
    this->send_cmd_(b);
  }

  bool display_state() const { return this->have_small_ && (this->small_body_[12] & 0x10); }

 protected:
  bool fresh_() { return this->have_small_ && (millis() - this->last_small_ms_) < 15000; }

  // returns true if action changed
  bool update_action_() {
    climate::ClimateAction a;
    bool busy = this->inverter_power_ > 3;
    switch (this->mode) {
      case climate::CLIMATE_MODE_OFF: a = climate::CLIMATE_ACTION_OFF; break;
      case climate::CLIMATE_MODE_FAN_ONLY: a = climate::CLIMATE_ACTION_FAN; break;
      case climate::CLIMATE_MODE_COOL: a = busy ? climate::CLIMATE_ACTION_COOLING : climate::CLIMATE_ACTION_IDLE; break;
      case climate::CLIMATE_MODE_HEAT: a = busy ? climate::CLIMATE_ACTION_HEATING : climate::CLIMATE_ACTION_IDLE; break;
      case climate::CLIMATE_MODE_DRY: a = climate::CLIMATE_ACTION_DRYING; break;
      default:
        a = busy ? (this->current_temperature > this->target_temperature
                        ? climate::CLIMATE_ACTION_COOLING
                        : climate::CLIMATE_ACTION_HEATING)
                 : climate::CLIMATE_ACTION_IDLE;
        break;
    }
    bool changed = a != this->action;
    this->action = a;
    return changed;
  }

  void control(const climate::ClimateCall &call) override {
    if (!this->fresh_()) {
      ESP_LOGW(TAG, "control: last small status stale/missing — command refused (is polling on?)");
      return;
    }
    uint8_t b[15];
    memcpy(b, this->small_body_, 15);
    b[0] = 0x01;   // CMD = SET_PARAMS
    b[1] = 0x01;
    b[4] &= 0x80;  // zero minutes-since-IR, keep frac flag for now

    if (call.get_mode().has_value()) {
      auto m = *call.get_mode();
      if (m == climate::CLIMATE_MODE_OFF) {
        b[10] &= ~0x20;
      } else {
        b[10] |= 0x20;
        uint8_t mv = 0x00;  // HEAT_COOL / auto
        if (m == climate::CLIMATE_MODE_COOL) mv = 0x20;
        else if (m == climate::CLIMATE_MODE_DRY) mv = 0x40;
        else if (m == climate::CLIMATE_MODE_HEAT) mv = 0x80;
        else if (m == climate::CLIMATE_MODE_FAN_ONLY) mv = 0xC0;
        b[7] = (b[7] & ~0xE0) | mv;
      }
      this->mode = m;
    }

    // Temperature is ALWAYS re-encoded — explicitly when the call carries a
    // target, otherwise preserving the stored value. The unit decrements
    // non-.0/.5 tenths by 0.1 on EVERY accepted SET (verified live: sent
    // .8->.7, .7->.6, .6->.5, .1->.0; .0/.5 are fixed points), so without
    // compensation every fan/swing/preset command erodes the setpoint by
    // 0.1 C until it hits .5/.0 (found in the wild: an HA swing command
    // knocked 22.7 -> 22.6). Compensation: send tenths+1 for non-.0/.5.
    {
      float t;
      if (call.get_target_temperature().has_value()) {
        t = *call.get_target_temperature();
        if (t < 16.0f) t = 16.0f;
        if (t > 32.0f) t = 32.0f;
        t = roundf(t * 10.0f) / 10.0f;
        this->target_temperature = t;
      } else {
        t = 8.0f + ((b[2] >> 3) & 0x1F) + b[14] / 10.0f;  // preserve stored
      }
      uint8_t send_int = (uint8_t) t;
      uint8_t send_tenths = ((uint8_t) lroundf(t * 10.0f)) % 10;
      if (send_tenths != 0 && send_tenths != 5) {
        send_tenths += 1;                  // unit will store tenths-1 = intended
        if (send_tenths == 10) { send_tenths = 0; send_int += 1; }
      }
      b[2] = (b[2] & 0x07) | (uint8_t)((send_int - 8) << 3);
      b[14] = send_tenths;
      b[4] = (b[4] & 0x7F) | (send_tenths == 5 ? 0x80 : 0x00);
    }

    if (call.get_fan_mode().has_value()) {
      auto f = *call.get_fan_mode();
      if (f == climate::CLIMATE_FAN_QUIET) {
        b[6] |= 0x80;
      } else {
        b[6] &= ~0x80;
        uint8_t fv = 0xA0;  // auto
        if (f == climate::CLIMATE_FAN_HIGH) fv = 0x20;
        else if (f == climate::CLIMATE_FAN_MEDIUM) fv = 0x40;
        else if (f == climate::CLIMATE_FAN_LOW) fv = 0x60;
        b[5] = (b[5] & ~0xE0) | fv;
      }
      this->fan_mode = f;
    }

    if (call.get_swing_mode().has_value()) {
      auto s = *call.get_swing_mode();
      // 0 = swing, 7 = stop at current position
      b[2] = (b[2] & ~0x07) | (s == climate::CLIMATE_SWING_VERTICAL ? 0x00 : 0x07);
      this->swing_mode = s;
    }

    if (call.get_preset().has_value()) {
      auto p = *call.get_preset();
      b[6] = (b[6] & ~0x40) | (p == climate::CLIMATE_PRESET_BOOST ? 0x40 : 0x00);
      b[7] = (b[7] & ~0x04) | (p == climate::CLIMATE_PRESET_SLEEP ? 0x04 : 0x00);
      this->preset = p;
    }

    this->send_cmd_(b);
    this->update_action_();
    this->publish_state();  // optimistic; poll readback corrects within ~2 s
  }

  void send_cmd_(const uint8_t body[15]) {
    uint8_t p[25] = {0xBB, 0x00, 0x06, 0x80, 0x00, 0x00, 0x0F, 0x00};
    memcpy(&p[8], body, 15);
    uint32_t s = 0;
    for (int j = 0; j + 1 < 23; j += 2)
      s += ((uint32_t) p[j] << 8) | p[j + 1];
    s += (uint32_t) p[22] << 8;  // odd length: pad zero
    while (s >> 16)
      s = (s & 0xFFFF) + (s >> 16);
    uint16_t c = ~s & 0xFFFF;
    p[23] = c >> 8;
    p[24] = c & 0xFF;
    this->write_array(p, 25);
    char hex[80];
    int o = 0;
    for (int j = 8; j < 23 && o < 70; j++)
      o += snprintf(hex + o, sizeof(hex) - o, "%02X ", p[j]);
    ESP_LOGI(TAG, "SET sent, body: %s", hex);
    // request fresh readback immediately
    static const uint8_t req[12] = {0xBB, 0x00, 0x06, 0x80, 0x00, 0x00,
                                    0x02, 0x00, 0x11, 0x01, 0x2B, 0x7E};
    this->write_array(req, sizeof(req));
  }

  climate::ClimateTraits traits() override {
    auto t = climate::ClimateTraits();
    t.add_feature_flags(climate::CLIMATE_SUPPORTS_CURRENT_TEMPERATURE |
                        climate::CLIMATE_SUPPORTS_ACTION);
    t.set_supported_modes({climate::CLIMATE_MODE_OFF, climate::CLIMATE_MODE_COOL,
                           climate::CLIMATE_MODE_HEAT, climate::CLIMATE_MODE_DRY,
                           climate::CLIMATE_MODE_FAN_ONLY, climate::CLIMATE_MODE_HEAT_COOL});
    t.set_supported_fan_modes({climate::CLIMATE_FAN_AUTO, climate::CLIMATE_FAN_LOW,
                               climate::CLIMATE_FAN_MEDIUM, climate::CLIMATE_FAN_HIGH,
                               climate::CLIMATE_FAN_QUIET});
    t.set_supported_swing_modes({climate::CLIMATE_SWING_OFF, climate::CLIMATE_SWING_VERTICAL});
    t.set_supported_presets({climate::CLIMATE_PRESET_NONE, climate::CLIMATE_PRESET_BOOST,
                             climate::CLIMATE_PRESET_SLEEP});
    t.set_visual_min_temperature(16);
    t.set_visual_max_temperature(32);
    t.set_visual_target_temperature_step(0.5f);
    t.set_visual_current_temperature_step(0.1f);
    return t;
  }

  uint8_t small_body_[15]{};
  bool have_small_{false};
  bool first_publish_{true};
  uint32_t last_small_ms_{0};
  uint8_t inverter_power_{0};
};

}  // namespace della_ac
}  // namespace esphome
