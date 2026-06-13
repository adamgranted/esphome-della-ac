#!/usr/bin/env python3
"""Incremental write-test ladder for the Della AC climate entity.
Each step: send one climate command, wait for the unit's readback to confirm,
abort to safe-restore on mismatch."""
import asyncio, inspect, os, sys, time
import yaml
from aioesphomeapi import APIClient
from aioesphomeapi.model import ClimateMode, ClimateFanMode, ClimateSwingMode, ClimatePreset

async def maybe_await(r):
    if inspect.isawaitable(r):
        return await r

async def main():
    secrets = yaml.safe_load(open('secrets.yaml'))
    cli = APIClient(os.environ.get("DELLA_HOST", "della-slwf.local"), 6053, None, noise_psk=secrets['api_encryption_key'])
    await cli.connect(login=True)
    entities, _ = await cli.list_entities_services()
    clim = next(e for e in entities if type(e).__name__ == 'ClimateInfo')
    state = {}
    def on_state(s):
        if s.key == clim.key:
            state['s'] = s
            state['t'] = time.time()
    cli.subscribe_states(on_state)
    await asyncio.sleep(3)

    async def cmd(**kw):
        await maybe_await(cli.climate_command(clim.key, **kw))

    async def wait_for(desc, pred, timeout=12):
        t0 = time.time()
        while time.time() - t0 < timeout:
            s = state.get('s')
            if s and pred(s):
                print(f"  PASS  {desc}  (after {time.time()-t0:.1f}s)", flush=True)
                return True
            await asyncio.sleep(0.5)
        s = state.get('s')
        print(f"  FAIL  {desc} — state: mode={s.mode} fan={s.fan_mode} swing={s.swing_mode} "
              f"preset={s.preset} target={s.target_temperature:.1f}", flush=True)
        return False

    async def restore_safe():
        print("RESTORING SAFE STATE: cool / 22.6 / fan auto / swing off / preset none", flush=True)
        await cmd(mode=ClimateMode.COOL, target_temperature=22.6,
                  fan_mode=ClimateFanMode.AUTO, swing_mode=ClimateSwingMode.OFF,
                  preset=ClimatePreset.NONE)
        await wait_for("safe state", lambda s: s.mode == ClimateMode.COOL and
                       abs(s.target_temperature - 22.6) < 0.15 and
                       s.fan_mode == ClimateFanMode.AUTO)

    steps_passed = []
    steps_failed = []

    async def step(name, kwargs, pred, settle=2, timeout=12):
        print(f"STEP: {name}", flush=True)
        await cmd(**kwargs)
        ok = await wait_for(name, pred, timeout)
        (steps_passed if ok else steps_failed).append(name)
        await asyncio.sleep(settle)
        return ok

    # --- 1. fan speeds ---
    for fm, nm in ((ClimateFanMode.LOW, 'fan LOW'), (ClimateFanMode.MEDIUM, 'fan MEDIUM'),
                   (ClimateFanMode.HIGH, 'fan HIGH'), (ClimateFanMode.AUTO, 'fan AUTO')):
        if not await step(nm, dict(fan_mode=fm), lambda s, fm=fm: s.fan_mode == fm):
            await restore_safe(); return report(steps_passed, steps_failed)

    # quiet/mute — unit may not support; non-fatal
    await step('fan QUIET (mute)', dict(fan_mode=ClimateFanMode.QUIET),
               lambda s: s.fan_mode == ClimateFanMode.QUIET, timeout=8)
    await step('fan AUTO (restore)', dict(fan_mode=ClimateFanMode.AUTO),
               lambda s: s.fan_mode == ClimateFanMode.AUTO)

    # --- 2. swing ---
    if not await step('swing VERTICAL', dict(swing_mode=ClimateSwingMode.VERTICAL),
                      lambda s: s.swing_mode == ClimateSwingMode.VERTICAL):
        await restore_safe(); return report(steps_passed, steps_failed)
    if not await step('swing OFF', dict(swing_mode=ClimateSwingMode.OFF),
                      lambda s: s.swing_mode == ClimateSwingMode.OFF):
        await restore_safe(); return report(steps_passed, steps_failed)

    # --- 3. target temperature via climate ---
    if not await step('target 23.1', dict(target_temperature=23.1),
                      lambda s: abs(s.target_temperature - 23.0) < 0.25):
        await restore_safe(); return report(steps_passed, steps_failed)
    await step('target 22.6 (restore)', dict(target_temperature=22.6),
               lambda s: abs(s.target_temperature - 22.6) < 0.15)

    # --- 4. presets ---
    await step('preset BOOST (turbo)', dict(preset=ClimatePreset.BOOST),
               lambda s: s.preset == ClimatePreset.BOOST, timeout=8)
    await step('preset NONE', dict(preset=ClimatePreset.NONE),
               lambda s: s.preset == ClimatePreset.NONE)
    await step('preset SLEEP', dict(preset=ClimatePreset.SLEEP),
               lambda s: s.preset == ClimatePreset.SLEEP, timeout=8)
    await step('preset NONE', dict(preset=ClimatePreset.NONE),
               lambda s: s.preset == ClimatePreset.NONE)

    # --- 5. modes (brief dwell, return to cool each time) ---
    for m, nm in ((ClimateMode.FAN_ONLY, 'mode FAN_ONLY'), (ClimateMode.DRY, 'mode DRY'),
                  (ClimateMode.HEAT_COOL, 'mode AUTO/HEAT_COOL'), (ClimateMode.HEAT, 'mode HEAT')):
        ok = await step(nm, dict(mode=m), lambda s, m=m: s.mode == m, settle=4)
        ok2 = await step('mode COOL (return)', dict(mode=ClimateMode.COOL),
                         lambda s: s.mode == ClimateMode.COOL, settle=4)
        if not ok2:
            await restore_safe(); return report(steps_passed, steps_failed)

    # --- 6. power off / on ---
    if await step('mode OFF (power off)', dict(mode=ClimateMode.OFF),
                  lambda s: s.mode == ClimateMode.OFF, settle=6):
        await step('mode COOL (power on)', dict(mode=ClimateMode.COOL),
                   lambda s: s.mode == ClimateMode.COOL, settle=4)
    else:
        await restore_safe()

    await restore_safe()
    report(steps_passed, steps_failed)
    s = state.get('s')
    print(f"\nFINAL: mode={s.mode} target={s.target_temperature:.1f} fan={s.fan_mode} "
          f"swing={s.swing_mode} preset={s.preset} action={s.action} current={s.current_temperature:.1f}")
    await cli.disconnect()

def report(p, f):
    print(f"\n==== {len(p)} passed, {len(f)} failed ====")
    for n in f:
        print(f"  FAILED: {n}")


if __name__ == '__main__':
    asyncio.run(main())
