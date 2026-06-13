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
}

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
