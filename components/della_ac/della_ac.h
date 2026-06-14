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
// Protocol facts (full map: docs/PROTOCOL.md):
//   small body: [2] vlouver(0-2: 0=swing)|setpoint-8<<3   [3] hlouver(5-7: 0=swing)
//               [4] ir-min(0-5)|frac.5(7)
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

    bool v_swing = (body[2] & 0x07) == 0x00;   // v_louver 0 = swing, 7 = fixed
    bool h_swing = (body[3] & 0xE0) == 0x00;   // h_louver 0 = swing, 0x20 = fixed
    climate::ClimateSwingMode swing =
        v_swing ? (h_swing ? climate::CLIMATE_SWING_BOTH : climate::CLIMATE_SWING_VERTICAL)
                : (h_swing ? climate::CLIMATE_SWING_HORIZONTAL : climate::CLIMATE_SWING_OFF);

    climate::ClimatePreset preset = climate::CLIMATE_PRESET_NONE;
    if (body[6] & 0x40)
      preset = climate::CLIMATE_PRESET_BOOST;       // turbo
    else if (body[7] & 0x04)
      preset = climate::CLIMATE_PRESET_SLEEP;

    bool changed = mode != this->mode || fan != this->fan_mode || swing != this->swing_mode ||
                   preset != this->preset || fabsf(target - this->target_temperature) > 0.05f;

    // Clean, human-readable state logging at INFO: one summary line on first
    // sync, then only what actually changed. No per-poll spam.
    if (this->first_publish_) {
      ESP_LOGI(TAG, "State: %s, target %.1f C, fan %s",
               LOG_STR_ARG(climate::climate_mode_to_string(mode)), target,
               LOG_STR_ARG(climate::climate_fan_mode_to_string(fan)));
    } else {
      if (mode != this->mode)
        ESP_LOGI(TAG, "Mode: %s -> %s", LOG_STR_ARG(climate::climate_mode_to_string(this->mode)),
                 LOG_STR_ARG(climate::climate_mode_to_string(mode)));
      if (fabsf(target - this->target_temperature) > 0.05f)
        ESP_LOGI(TAG, "Target: %.1f -> %.1f C", this->target_temperature, target);
      if (fan != this->fan_mode)
        ESP_LOGI(TAG, "Fan: %s -> %s",
                 LOG_STR_ARG(climate::climate_fan_mode_to_string(this->fan_mode.value())),
                 LOG_STR_ARG(climate::climate_fan_mode_to_string(fan)));
      if (swing != this->swing_mode)
        ESP_LOGI(TAG, "Swing: %s -> %s", LOG_STR_ARG(climate::climate_swing_mode_to_string(this->swing_mode)),
                 LOG_STR_ARG(climate::climate_swing_mode_to_string(swing)));
      if (preset != this->preset)
        ESP_LOGI(TAG, "Preset: %s -> %s",
                 LOG_STR_ARG(climate::climate_preset_to_string(this->preset.value())),
                 LOG_STR_ARG(climate::climate_preset_to_string(preset)));
    }

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

  void ingest_big(float indoor, uint8_t inverter_power, uint8_t fan_actual) {
    this->inverter_power_ = inverter_power;
    this->fan_actual_ = fan_actual;
    bool changed = isnan(this->current_temperature) ||
                   fabsf(indoor - this->current_temperature) > 0.05f;
    this->current_temperature = indoor;
    changed = this->update_action_() || changed;
    if (changed)
      this->publish_state();
  }

  // Single-bit feature toggles in the small-status body, exposed as YAML
  // template switches. All route through set_feature_(), which rebuilds the SET
  // from the latest status WITH setpoint compensation, so toggling a feature
  // never erodes a fractional setpoint.
  void set_display(bool on) { this->set_feature_(12, 0x10, on); }     // panel light
  bool display_state() const { return this->feature_state_(12, 0x10); }
  void set_health(bool on) { this->set_feature_(10, 0x03, on); }      // ionizer/health (IR-confirmed: bits 0+1)
  bool health_state() const { return this->feature_state_(10, 0x03); }
  void set_eco(bool on) { this->set_feature_(10, 0x08, on); }         // energy-save (IR-confirmed [10].3)
  bool eco_state() const { return this->feature_state_(10, 0x08); }

  // Self-clean and Anti-fungus are OFF-STATE functions (verified live on the
  // unit): the AC only honors them with the power bit cleared, so they force
  // power off in the SET rather than being plain set_feature_() toggles.
  void press_iclean() {                       // start the evaporator self-clean cycle
    uint8_t b[15];
    if (!this->prepare_set_(b))
      return;
    b[10] = (b[10] & ~0x20) | 0x04;           // power off + iClean active (observed state = 0x04)
    this->send_cmd_(b);
  }
  // "actively self-cleaning" = clean bit set AND power off (the real cycle
  // reports power-off + 0x04). A stray 0x04 on a powered unit is not a cycle.
  bool iclean_state() const { return this->have_small_ && (this->small_body_[10] & 0x24) == 0x04; }
  void set_antifungus(bool on) {              // arm/disarm the shutdown dry cycle ([12].3)
    uint8_t b[15];
    if (!this->prepare_set_(b))
      return;
    b[10] &= ~0x20;                           // anti-f is only accepted with the unit off
    if (on) b[12] |= 0x08; else b[12] &= ~0x08;
    this->send_cmd_(b);
  }
  bool antifungus_state() const { return this->feature_state_(12, 0x08); }

  // Big-frame [13] = reported live blower speed. Mapped on the unit (2/4/6).
  const char *fan_actual_str() const {
    switch (this->fan_actual_) {
      case 2: return "low";
      case 4: return "med";
      case 6: return "high";
      case 0: return "idle";
      default: return "?";
    }
  }

  // One readable summary for the "Status" text sensor. Folding setpoint, mode,
  // fan, swing, preset and active features into a single string means a
  // setpoint change (a climate attribute HA's logbook ignores) — and every
  // other change — lands as one state change in the HA history/logbook.
  std::string status_line() {
    // NOTE: build only from plain string literals. On ESP8266 the climate_*_to_string
    // helpers return LOG_STR (PROGMEM) pointers; feeding those to std::string does a
    // byte-wise flash read that faults and crash-loops the device.
    if (!this->have_small_)
      return "--";
    if (this->mode == climate::CLIMATE_MODE_OFF) {
      std::string s = this->iclean_state() ? "self-clean" : "off";
      if (this->antifungus_state()) s += " | anti-fungus armed";
      return s;
    }
    const char *mode_s;
    switch (this->mode) {
      case climate::CLIMATE_MODE_COOL: mode_s = "cool"; break;
      case climate::CLIMATE_MODE_HEAT: mode_s = "heat"; break;
      case climate::CLIMATE_MODE_DRY: mode_s = "dry"; break;
      case climate::CLIMATE_MODE_FAN_ONLY: mode_s = "fan"; break;
      default: mode_s = "auto"; break;  // HEAT_COOL
    }
    std::string s = mode_s;
    bool degf = this->small_body_[7] & 0x02;
    float disp = degf ? this->target_temperature * 9.0f / 5.0f + 32.0f : this->target_temperature;
    char buf[24];
    snprintf(buf, sizeof(buf), degf ? " %.0fF" : " %.1fC", disp);
    s += buf;
    const char *fan_s;
    switch (this->fan_mode.value()) {
      case climate::CLIMATE_FAN_HIGH: fan_s = "high"; break;
      case climate::CLIMATE_FAN_MEDIUM: fan_s = "med"; break;
      case climate::CLIMATE_FAN_LOW: fan_s = "low"; break;
      case climate::CLIMATE_FAN_QUIET: fan_s = "quiet"; break;
      default: fan_s = "auto"; break;
    }
    s += " | fan "; s += fan_s;
    // In Auto the unit picks a speed — surface what it's actually running.
    if (this->fan_mode.value() == climate::CLIMATE_FAN_AUTO && this->fan_actual_) {
      s += " ("; s += this->fan_actual_str(); s += ")";
    }
    if (this->swing_mode != climate::CLIMATE_SWING_OFF) {
      const char *sw;
      switch (this->swing_mode) {
        case climate::CLIMATE_SWING_BOTH: sw = "both"; break;
        case climate::CLIMATE_SWING_VERTICAL: sw = "vert"; break;
        case climate::CLIMATE_SWING_HORIZONTAL: sw = "horiz"; break;
        default: sw = "off"; break;
      }
      s += " | swing "; s += sw;
    }
    if (this->preset.value() == climate::CLIMATE_PRESET_BOOST) s += " | turbo";
    else if (this->preset.value() == climate::CLIMATE_PRESET_SLEEP) s += " | sleep";
    if (this->health_state()) s += " | health";
    if (this->eco_state()) s += " | eco";
    if (this->antifungus_state()) s += " | anti-fungus";
    return s;
  }

 protected:
  bool fresh_() { return this->have_small_ && (millis() - this->last_small_ms_) < 15000; }

  // Rebuild a SET body from the latest small status: command bytes set,
  // IR-minutes cleared, and the *current* setpoint re-encoded with the
  // tenths+1 compensation (so a feature toggle doesn't erode a fractional
  // setpoint — see control()). Returns false if there's no fresh status.
  bool prepare_set_(uint8_t b[15]) {
    if (!this->fresh_()) {
      ESP_LOGW(TAG, "set: no fresh small status, refusing");
      return false;
    }
    memcpy(b, this->small_body_, 15);
    b[0] = 0x01; b[1] = 0x01;
    b[4] &= 0x80;
    float t = 8.0f + ((b[2] >> 3) & 0x1F) + b[14] / 10.0f;
    uint8_t send_int = (uint8_t) t;
    uint8_t send_tenths = ((uint8_t) lroundf(t * 10.0f)) % 10;
    if (send_tenths != 0 && send_tenths != 5) {
      send_tenths += 1;
      if (send_tenths == 10) { send_tenths = 0; send_int += 1; }
    }
    b[2] = (b[2] & 0x07) | (uint8_t)((send_int - 8) << 3);
    b[14] = send_tenths;
    b[4] = (b[4] & 0x7F) | (send_tenths == 5 ? 0x80 : 0x00);
    return true;
  }

  void set_feature_(uint8_t idx, uint8_t mask, bool on) {
    uint8_t b[15];
    if (!this->prepare_set_(b))
      return;
    if (on) b[idx] |= mask; else b[idx] &= ~mask;
    this->send_cmd_(b);
  }
  bool feature_state_(uint8_t idx, uint8_t mask) const {
    return this->have_small_ && (this->small_body_[idx] & mask);
  }

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
      bool v = s == climate::CLIMATE_SWING_VERTICAL || s == climate::CLIMATE_SWING_BOTH;
      bool h = s == climate::CLIMATE_SWING_HORIZONTAL || s == climate::CLIMATE_SWING_BOTH;
      b[2] = (b[2] & ~0x07) | (v ? 0x00 : 0x07);   // v_louver: 0 = swing, 7 = fixed
      b[3] = (b[3] & ~0xE0) | (h ? 0x00 : 0x20);   // h_louver: 0 = swing, 0x20 = fixed
      this->swing_mode = s;
    }

    if (call.get_preset().has_value()) {
      auto p = *call.get_preset();
      b[6] = (b[6] & ~0x40) | (p == climate::CLIMATE_PRESET_BOOST ? 0x40 : 0x00);
      b[7] = (b[7] & ~0x04) | (p == climate::CLIMATE_PRESET_SLEEP ? 0x04 : 0x00);
      this->preset = p;
    }

    // Self-clean is mutually exclusive with a running mode: never command (or
    // leave) the clean bit set while powered on, else the unit gets stuck at
    // 0x24 (cooling + clean-flag) after a cycle is cancelled with power.
    if (b[10] & 0x20)
      b[10] &= ~0x04;

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
    t.set_supported_swing_modes({climate::CLIMATE_SWING_OFF, climate::CLIMATE_SWING_VERTICAL,
                                 climate::CLIMATE_SWING_HORIZONTAL, climate::CLIMATE_SWING_BOTH});
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
  uint8_t fan_actual_{0};   // big-frame [13]: reported live blower speed (2/4/6 = low/med/high)
};

}  // namespace della_ac
}  // namespace esphome
