"""Pure-logic tests for the AUX protocol decode tools.

These do not need a device. They exercise:
  - the CRC16 (RFC1071) implementation,
  - that the firmware's hard-coded TX frames carry correct CRCs,
  - the full pulse -> bits -> 8E1 bytes pipeline against a real captured frame.
"""
import pytest

from pulse2bytes import to_bits, to_bytes, crc16_rfc1071
from analyze_bursts import parse, lows_sig, HB

# Real AUX frames (full, including the trailing 2 CRC bytes). The first four are
# the exact byte strings the firmware transmits, so this also guards them.
FRAMES = {
    "ping":          "BB 00 01 00 00 00 00 00 43 FF",
    "small_request": "BB 00 06 80 00 00 02 00 11 01 2B 7E",
    "big_request":   "BB 00 06 80 00 00 02 00 21 01 1B 7E",
    "ping_answer":   "BB 00 01 80 01 00 08 00 1C 27 00 00 00 00 00 00 1E 58",
    "big_status":    "BB 00 07 00 00 00 18 00 01 20 E4 21 00 02 55 35 2E 2E 2E "
                     "64 3A 3D 40 39 14 06 A4 2C 08 00 00 03 54 47",
    # Della Motto JA 12K (model 12K1VRH-20S-JA, issue #11). Same AUX big-status
    # layout, but 35 bytes: one extra trailing pad byte before the 2-byte CRC.
    "big_status_motto": "BB 00 07 00 00 00 19 00 01 21 E0 20 00 00 00 36 37 37 37 "
                        "64 38 39 38 39 00 00 51 2D 00 00 00 03 00 13 49",
    # Motto JA, captured while actively cooling (setpoint 18C): inverter power and
    # fan now non-zero, confirming those fields decode under the shared map.
    "big_status_motto_active": "BB 00 07 00 00 00 19 00 01 21 E0 21 00 06 6C 36 32 32 32 "
                               "64 35 39 38 39 33 0F 81 4D 0E 00 00 00 00 43 1A",
    # Motto JA alternate status frame (type 0x2C): same layout/offsets as 0x21,
    # interleaved with the polled frame. The firmware accepts it alongside 0x20/0x21.
    "big_status_motto_alt2c":  "BB 00 07 00 00 00 19 00 01 2C E0 21 00 06 6B 36 33 33 33 "
                               "64 35 39 38 39 33 0F 81 4D 0E 00 00 00 00 42 0E",
}

# Big-status field map (mirrors the YAML lambda). Both the 34-byte 048-MS frame
# and the 35-byte Motto frame use these exact offsets — that equivalence is the
# whole reason a single firmware serves both. Keyed by FRAMES name.
BIG_STATUS_EXPECTED = {
    "big_status":              dict(indoor=21.3, coil=14, outdoor=26, compressor=32, inverter=20, fan=2),
    "big_status_motto":        dict(indoor=22.3, coil=23, outdoor=24, compressor=24, inverter=0,  fan=0),
    "big_status_motto_active": dict(indoor=22.0, coil=18, outdoor=21, compressor=24, inverter=51, fan=6),
    "big_status_motto_alt2c":  dict(indoor=22.0, coil=19, outdoor=21, compressor=24, inverter=51, fan=6),
}


def decode_big_status(data: bytes) -> dict:
    """Decode an AUX big-status frame exactly as della-ac.base.yaml does."""
    assert data[2] == 0x07 and data[9] in (0x20, 0x21, 0x2C), "not a big-status frame"
    assert data[19] == 0x64 and data[23] == 0x39, "internal markers misaligned"
    return dict(
        indoor=round(data[15] - 0x20 + (data[31] & 0x0F) / 10.0, 1),
        coil=data[16] - 0x20,
        outdoor=data[20] - 0x20,
        compressor=data[22] - 0x20,
        inverter=data[24],
        fan=data[13],
    )

# A real captured heartbeat burst (ESPHome remote.raw timings, microseconds).
# Decodes to the ping frame above.
HEARTBEAT_TIMINGS = [
    -206, 412, -206, 619, -205, 206, -206, 258, -2060, 258, -206, 206, -1442,
    464, -2060, 258, -2060, 258, -2060, 257, -2061, 412, -2060, 258, -206, 412,
    -825, 205, -206, 464, -206, 1648, -206,
]


@pytest.mark.parametrize("name,hexstr", FRAMES.items())
def test_crc_matches_frame(name, hexstr):
    data = bytes.fromhex(hexstr.replace(" ", ""))
    body, expected = data[:-2], (data[-2] << 8) | data[-1]
    assert crc16_rfc1071(body) == expected, f"{name} CRC mismatch"


@pytest.mark.parametrize("name", BIG_STATUS_EXPECTED)
def test_big_status_decodes_for_both_models(name):
    # The 34-byte (048-MS) and 35-byte (Motto JA) frames decode to sensible,
    # self-consistent values under one shared field map. The firmware gate must
    # therefore accept both lengths; this guards against it regressing to 34-only.
    data = bytes.fromhex(FRAMES[name].replace(" ", ""))
    assert len(data) in (34, 35)
    assert decode_big_status(data) == BIG_STATUS_EXPECTED[name]


def test_heartbeat_decodes_to_ping():
    data = bytes(v for v, _ in to_bytes(to_bits(HEARTBEAT_TIMINGS)))
    assert data == bytes.fromhex(FRAMES["ping"].replace(" ", ""))


def test_heartbeat_no_parity_errors():
    assert all(ok for _, ok in to_bytes(to_bits(HEARTBEAT_TIMINGS)))


def test_lows_signature_classifies_heartbeat(tmp_path):
    log = tmp_path / "hb.log"
    timings = ", ".join(str(v) for v in HEARTBEAT_TIMINGS)
    log.write_text(f"[12:30:10.155][I][remote.raw:035]: Received Raw: {timings}\n")
    bursts = parse(str(log))
    assert len(bursts) == 1
    ts, t = bursts[0]
    assert len(t) == len(HEARTBEAT_TIMINGS)
    assert lows_sig(t) == HB


def test_parse_merges_chunked_burst(tmp_path):
    # ESPHome splits long bursts across indented continuation lines; parse()
    # must stitch them back into a single burst.
    log = tmp_path / "chunked.log"
    log.write_text(
        "[01:02:03.400][I][remote.raw:026]: Received Raw: -206, 412, -206, 618\n"
        "[01:02:03.400][I][remote.raw:026]:   -206, 206, -2060, 258\n"
    )
    bursts = parse(str(log))
    assert len(bursts) == 1
    assert len(bursts[0][1]) == 8
