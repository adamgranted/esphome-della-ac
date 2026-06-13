#!/usr/bin/env python3
"""Guided IR-remote capture sweep for the Della 048-MS (AUX protocol).

Run from a directory containing secrets.yaml (api_encryption_key); device host
from $DELLA_HOST (default della-slwf.local). Requires the v6+ firmware with
1 s polling (the unit's settings readback lands within ~2 s of any IR press).

Flow: for each scripted step you press the named remote button, then Enter.
The script diffs the unit's 15-byte settings body before/after, decodes known
fields, and LOUDLY flags never-before-seen bits. Ends with a free-form loop
for any remaining untested buttons. Everything (raw frames + diffs) is logged
to captures/ir-sweep-<timestamp>.{json,md}.

Keys at each prompt: Enter=captured  s=skip  r=redo last  q=quit (writes report)
"""
import asyncio, json, os, sys, time
from datetime import datetime

import yaml
from aioesphomeapi import APIClient

# ---- known field map: (byte, mask) -> name, decoder ------------------------
FAN = {0x20: 'HIGH', 0x40: 'MED', 0x60: 'LOW', 0xA0: 'AUTO', 0x00: 'none/0x00'}
MODE = {0x00: 'auto', 0x20: 'cool', 0x40: 'dry', 0x80: 'heat', 0xC0: 'fan'}
VLOUV = {0: 'swing', 1: 'top', 2: 'pos2', 3: 'mid', 4: 'pos4', 5: 'bottom', 6: 'pos6', 7: 'stop'}

FIELDS = [
    (2, 0x07, 'v_louver', lambda v: VLOUV.get(v, v)),
    (2, 0xF8, 'setpoint_int', lambda v: f"{8 + (v >> 3)}C"),
    (3, 0xE0, 'h_louver', lambda v: v >> 5),
    (3, 0x1F, 'byte3_low_UNKNOWN', lambda v: bin(v)),
    (4, 0x3F, 'min_since_ir', lambda v: v),
    (4, 0x40, 'byte4_bit6_UNKNOWN', lambda v: v >> 6),
    (4, 0x80, 'half_degree_flag', lambda v: v >> 7),
    (5, 0xE0, 'fan_speed', lambda v: FAN.get(v, hex(v))),
    (5, 0x1F, 'byte5_low_UNKNOWN', lambda v: bin(v)),
    (6, 0x40, 'turbo', lambda v: v >> 6),
    (6, 0x80, 'mute', lambda v: v >> 7),
    (6, 0x3F, 'byte6_low_UNKNOWN', lambda v: bin(v)),
    (7, 0xE0, 'mode', lambda v: MODE.get(v, hex(v))),
    (7, 0x04, 'sleep', lambda v: v >> 2),
    (7, 0x02, 'fahrenheit_display', lambda v: v >> 1),
    (7, 0x19, 'byte7_other_UNKNOWN', lambda v: bin(v)),
    (8, 0xFF, 'byte8_UNKNOWN', lambda v: hex(v)),
    (9, 0xFF, 'byte9_UNKNOWN', lambda v: hex(v)),
    (10, 0x20, 'power', lambda v: v >> 5),
    (10, 0x04, 'iclean', lambda v: v >> 2),
    (10, 0x02, 'health_ion', lambda v: v >> 1),
    (10, 0x01, 'health_status', lambda v: v),
    (10, 0xD8, 'byte10_other_UNKNOWN', lambda v: bin(v)),
    (11, 0xFF, 'byte11_UNKNOWN', lambda v: hex(v)),
    (12, 0x10, 'display', lambda v: v >> 4),
    (12, 0x08, 'mildew', lambda v: v >> 3),
    (12, 0xE7, 'byte12_other_UNKNOWN', lambda v: bin(v)),
    (13, 0x80, 'pow_limit_on', lambda v: v >> 7),
    (13, 0x7F, 'pow_limit_val', lambda v: v),
    (14, 0xFF, 'setpoint_tenths', lambda v: v),
]

STEPS = [
    ("POWER off",        "Press POWER to turn the unit OFF"),
    ("POWER on",         "Press POWER to turn the unit back ON"),
    ("MODE -> heat",     "Press MODE until the display shows HEAT"),
    ("MODE -> dry",      "Press MODE until the display shows DRY"),
    ("MODE -> fan",      "Press MODE until the display shows FAN"),
    ("MODE -> auto",     "Press MODE until the display shows AUTO"),
    ("MODE -> cool",     "Press MODE until the display shows COOL (back to normal)"),
    ("TEMP +1",          "Press TEMP UP once"),
    ("TEMP -1",          "Press TEMP DOWN once (back to original)"),
    ("FAN low",          "Press FAN until LOW"),
    ("FAN med",          "Press FAN until MEDIUM"),
    ("FAN high",         "Press FAN until HIGH"),
    ("FAN auto",         "Press FAN until AUTO"),
    ("TURBO on",         "Press TURBO/POWERFUL on"),
    ("TURBO off",        "Press TURBO/POWERFUL off"),
    ("SLEEP on",         "Press SLEEP on"),
    ("SLEEP off",        "Press SLEEP off"),
    ("SWING vertical",   "Press SWING (vertical) to start swinging"),
    ("SWING stop",       "Press SWING again to stop"),
    ("DISPLAY/LED off",  "Press DISPLAY/LED to turn the panel display OFF"),
    ("DISPLAY/LED on",   "Press DISPLAY/LED back ON"),
    ("ECO",              "Press ECO (if present) — then press again to undo after capture"),
    ("HEALTH/ION",       "Press HEALTH/ION (if present)"),
    ("HEALTH/ION off",   "Press HEALTH/ION again to turn it off"),
    ("FOLLOW ME on",     "Press FOLLOW ME / I FEEL (if present) — remote becomes the sensor"),
    ("FOLLOW ME off",    "Press FOLLOW ME / I FEEL again to disable"),
    ("F/C toggle",       "Toggle the deg F / deg C display unit (if the remote supports it)"),
    ("F/C toggle back",  "Toggle the display unit back"),
]


def decode(body):
    out = {}
    for byte, mask, name, fn in FIELDS:
        v = body[byte] & mask
        shift_v = v
        out[name] = fn(shift_v)
    return out


def diff(before, after):
    changes = []
    for byte in range(15):
        x = before[byte] ^ after[byte]
        if not x:
            continue
        for fbyte, mask, name, fn in FIELDS:
            if fbyte == byte and (x & mask):
                changes.append({
                    'byte': byte, 'mask': f'0x{mask:02X}', 'field': name,
                    'before': str(fn(before[byte] & mask)),
                    'after': str(fn(after[byte] & mask)),
                    'unknown': 'UNKNOWN' in name,
                })
    return changes


async def ainput(prompt):
    return (await asyncio.to_thread(input, prompt)).strip().lower()


async def main():
    secrets = yaml.safe_load(open('secrets.yaml'))
    host = os.environ.get('DELLA_HOST', 'della-slwf.local')
    cli = APIClient(host, 6053, None, noise_psk=secrets['api_encryption_key'])
    await cli.connect(login=True)
    entities, _ = await cli.list_entities_services()
    small_key = next(e.key for e in entities if e.name == 'Last small frame')
    big_key = next(e.key for e in entities if e.name == 'Last status frame')

    latest = {'small': None, 'small_t': 0, 'frames': []}

    def on_state(s):
        if s.key == small_key and getattr(s, 'state', ''):
            b = bytes.fromhex(s.state.replace(' ', ''))
            if len(b) == 25:
                latest['small'] = b[8:23]
                latest['small_t'] = time.time()
                latest['frames'].append((time.time(), 'small', s.state))
        elif s.key == big_key and getattr(s, 'state', ''):
            latest['frames'].append((time.time(), 'big', s.state))

    cli.subscribe_states(on_state)

    print(f"Connected to {host}. Waiting for settings baseline...")
    t0 = time.time()
    while latest['small'] is None:
        if time.time() - t0 > 15:
            print("No small-status frames — is Auto poll ON? Aborting.")
            return
        await asyncio.sleep(0.5)

    session = {'started': datetime.now().isoformat(), 'baseline': latest['small'].hex(' '),
               'baseline_decoded': decode(latest['small']), 'steps': []}
    print("\nBaseline state:")
    for k, v in session['baseline_decoded'].items():
        if 'UNKNOWN' not in k:
            print(f"  {k:22s} {v}")

    async def capture(name, instruction):
        before = latest['small']
        print(f"\n=== {name} ===\n  {instruction}")
        ans = await ainput("  ...then press Enter (s=skip, q=quit): ")
        if ans == 's':
            session['steps'].append({'step': name, 'result': 'skipped'})
            return 'next'
        if ans == 'q':
            return 'quit'
        # wait for a frame that differs, or 8s of stable identical readbacks
        t0 = time.time()
        changed = None
        while time.time() - t0 < 8:
            await asyncio.sleep(0.5)
            cur = latest['small']
            if cur != before:
                changed = cur
                await asyncio.sleep(2.5)      # let the state settle (multi-press)
                changed = latest['small']
                break
        if changed is None:
            print("  !! no settings change detected (button may not map to the settings frame,")
            print("     or state was already there). r=redo, Enter=record-as-nochange, s=skip")
            ans = await ainput("  > ")
            if ans == 'r':
                return await capture(name, instruction)
            if ans == 's':
                session['steps'].append({'step': name, 'result': 'skipped'})
                return 'next'
            session['steps'].append({'step': name, 'result': 'no-change',
                                     'body': before.hex(' ')})
            return 'next'
        d = diff(before, changed)
        print(f"  CHANGES:")
        for c in d:
            flag = '  <<< NEW/UNKNOWN FIELD!' if c['unknown'] else ''
            print(f"    byte {c['byte']:2d} {c['field']:24s} {c['before']} -> {c['after']}{flag}")
        session['steps'].append({'step': name, 'result': 'captured',
                                 'before': before.hex(' '), 'after': changed.hex(' '),
                                 'diff': d})
        return 'next'

    quit_early = False
    for name, instruction in STEPS:
        if await capture(name, instruction) == 'quit':
            quit_early = True
            break

    # free-form remainder: anything left on the remote
    if not quit_early:
        print("\n=== Remaining buttons ===")
        print("Any untested buttons on the remote? Capture them now for full coverage.")
        while True:
            nm = (await asyncio.to_thread(
                input, "\nButton name (blank = done): ")).strip()
            if not nm:
                break
            if await capture(f"custom: {nm}", f"Press '{nm}' on the remote") == 'quit':
                break

    # report
    ts = datetime.now().strftime('%Y%m%d-%H%M')
    os.makedirs('captures', exist_ok=True)
    jpath = f'captures/ir-sweep-{ts}.json'
    session['ended'] = datetime.now().isoformat()
    session['final'] = latest['small'].hex(' ')
    session['final_decoded'] = decode(latest['small'])
    session['raw_frames'] = [(f"{t:.3f}", k, h) for t, k, h in latest['frames']]
    json.dump(session, open(jpath, 'w'), indent=1)

    mpath = f'captures/ir-sweep-{ts}.md'
    with open(mpath, 'w') as f:
        f.write(f"# IR sweep {session['started']}\n\nbaseline: `{session['baseline']}`\n\n")
        for s in session['steps']:
            f.write(f"## {s['step']} — {s['result']}\n")
            if s['result'] == 'captured':
                f.write(f"`{s['before']}` ->\n`{s['after']}`\n")
                for c in s['diff']:
                    f.write(f"- byte {c['byte']} **{c['field']}**: {c['before']} -> {c['after']}"
                            f"{'  **NEW/UNKNOWN**' if c['unknown'] else ''}\n")
            f.write("\n")
    print(f"\nWrote {jpath} and {mpath} ({len(session['steps'])} steps, "
          f"{len(latest['frames'])} raw frames archived).")

    # restore check
    if latest['small'] != bytes.fromhex(session['baseline'].replace(' ', '')):
        print("\nNOTE: final state differs from session baseline:")
        base = decode(bytes.fromhex(session['baseline'].replace(' ', '')))
        fin = session['final_decoded']
        for k in base:
            if base[k] != fin[k] and 'UNKNOWN' not in k and k != 'min_since_ir':
                print(f"  {k}: {base[k]} -> {fin[k]}")
        print("Restore manually with the remote, or via HA thermostat / test_ladder tools.")
    await cli.disconnect()


if __name__ == '__main__':
    asyncio.run(main())
