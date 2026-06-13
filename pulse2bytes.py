#!/usr/bin/env python3
"""Decode raw pulse timings (open-drain 4800 8E1 UART, AUX HVAC protocol)
into bytes + CRC16 (RFC1071) validation.

Usage: pulse2bytes.py <esphome-remote.raw-log-file>
Capture the log first with the della-la.yaml logic-analyzer build:
  esphome logs della-la.yaml | tee mycapture.log
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_bursts import parse, lows_sig, HB

T = 206.0

def to_bits(timings):
    bits = []
    for v in timings:
        if v < 0:
            n = round(-v / T)              # lows: timer-exact
            bits += [0] * n
        else:
            n = round((v + 77) / T)        # highs: release up to ~155us late
            bits += [1] * max(n, 1)
    return bits

def to_bytes(bits):
    out, i = [], 0
    while i < len(bits):
        if bits[i] == 1:
            i += 1
            continue
        frame = bits[i:i+11]
        if len(frame) < 10:
            break
        val = sum(frame[1+k] << k for k in range(8))
        par = frame[9]
        ok = (sum(frame[1:9]) + par) % 2 == 0
        out.append((val, ok))
        i += 11
    return out

def crc16_rfc1071(data):
    s = 0
    for i in range(0, len(data) - (len(data) % 2), 2):
        s += (data[i] << 8) | data[i+1]
    if len(data) % 2:
        s += data[-1] << 8
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF

def show(name, t):
    bits = to_bits(t)
    bs = to_bytes(bits)
    bad = [i for i, (v, ok) in enumerate(bs) if not ok]
    data = bytes(v for v, ok in bs)
    print(f"\n{name}: {len(data)} bytes, parity errors at {bad if bad else 'none'}")
    print(' '.join(f'{b:02X}' for b in data))
    if len(data) >= 4:
        body, crc = data[:-2], (data[-2] << 8) | data[-1]
        calc = crc16_rfc1071(body)
        print(f"CRC16 in frame: {crc:04X}  calculated: {calc:04X}  {'VALID' if crc == calc else 'MISMATCH'}")

if len(sys.argv) < 2:
    sys.exit(__doc__)
bursts = parse(sys.argv[1])
hb = next(t for ts, t in bursts if lows_sig(t) == HB and len(t) == 33)
show("HEARTBEAT", hb)
for idx, (ts, t) in enumerate([(ts, t) for ts, t in bursts if not (lows_sig(t) == HB and len(t) == 33)]):
    show(f"STATUS {idx} @ {ts}", t)
