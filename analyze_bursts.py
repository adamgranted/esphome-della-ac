#!/usr/bin/env python3
"""Parse ESPHome remote.raw dumps (incl. chunked multi-line bursts) from a log
capture, classify bursts, and report variance across novel bursts."""
import re, sys, statistics
from collections import Counter

T = 206.0  # base low-pulse unit, us

def parse(path):
    bursts = []  # (timestamp, [timings])
    cur = None; ts = None
    for line in open(path, errors='replace'):
        m = re.search(r'\[(\d\d:\d\d:\d\d\.\d+)\]\[I\]\[remote\.raw:\d+\]:\s+(.*)', line)
        if not m:
            continue
        t, body = m.groups()
        vals = [int(x) for x in re.findall(r'-?\d+', body)]
        if body.lstrip().startswith('Received Raw:'):
            if cur is not None:
                bursts.append((ts, cur))
            cur = vals; ts = t
        else:  # continuation line (indented)
            if cur is not None:
                cur.extend(vals)
    if cur is not None:
        bursts.append((ts, cur))
    return bursts

def lows_sig(t):
    out = []
    for v in t:
        if v < 0:
            n = round(-v / T)
            out.append(n)
    return tuple(out)

HB = (1,1,1,1,10,1,7,10,10,10,10,10,1,4,1,1,1)

def main(path):
    bursts = parse(path)
    print(f"{len(bursts)} bursts parsed")
    novel = []
    hb = 0
    for ts, t in bursts:
        s = lows_sig(t)
        if s == HB and len(t) == 33:
            hb += 1
        else:
            novel.append((ts, t, s))
    print(f"heartbeats: {hb}, novel: {len(novel)}")
    for ts, t, s in novel:
        print(f"\n--- NOVEL @ {ts}  pulses={len(t)} lows={len(s)}")
        print("  low seq :", ','.join(str(n) for n in s))
        print("  raw     :", ','.join(str(v) for v in t))
    # variance across novels of same length
    bylen = {}
    for ts, t, s in novel:
        bylen.setdefault(len(t), []).append((ts, t))
    for L, group in sorted(bylen.items()):
        if len(group) < 2:
            continue
        print(f"\n=== variance among {len(group)} novel bursts of {L} pulses ===")
        for i in range(L):
            vals = [g[1][i] for g in group]
            if max(vals) - min(vals) > 60:  # beyond jitter
                print(f"  pos {i:3d}: {sorted(set(vals))}")

if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'captures/della-logs7-longraw.txt')

# --- decode model (established 2026-06-12 ~12:50) ---------------------------
# Line: open-drain, idles HIGH (remote_receiver: negative = LOW, confirmed via
# ESPHome sign convention + idle-high physics).
# Transmitter: MCU bit-bangs on a ~51.5us tick grid (19200); LOW pulses are
# timer-exact multiples of 206us (= 4 ticks, ~4800-baud slots) and carry the
# data; rising "release" edges land 0-3 ticks late (deterministic per code
# path, jittery after 2060us lows) -> HIGH durations are separators, NOT data.
# Symbol = (low_us/206) - 1 -> digit 0..9. Digits 7,8 unobserved (~180 samples).
# Heartbeat digits: 00009069999903000 (17 symbols).
# Status bursts: variable length (82/83 symbols), shared 34-symbol prefix:
#   00009049993390651210032915000010 00...
# then variable middle, common "5212021020" -ish tail region, last 6-7 differ.

def digits(t):
    return ''.join(str(round(-v/206.0)-1) for v in t if v < 0)
