#!/usr/bin/env python3
"""Remote control of della-slwf entities over the ESPHome native API.
Usage:
  della_ctl.py list
  della_ctl.py press "<button name substr>" [watch_seconds]
  della_ctl.py number "<number name substr>" <value> [watch_seconds]
  della_ctl.py watch <seconds>
"""
import asyncio, sys, re
import yaml
from aioesphomeapi import APIClient

import os
HOST = os.environ.get("DELLA_HOST", "della-slwf.local")

async def main():
    secrets = yaml.safe_load(open('secrets.yaml'))
    key = secrets['api_encryption_key']
    cli = APIClient(HOST, 6053, None, noise_psk=key)
    await cli.connect(login=True)
    entities, _ = await cli.list_entities_services()
    bykey = {e.key: e for e in entities}

    interesting = re.compile(r'set test result|target setpoint|ac mode|ac power|indoor temp|bad frame', re.I)
    def on_state(state):
        e = bykey.get(state.key)
        if e is not None and interesting.search(e.name):
            val = getattr(state, 'state', None)
            print(f"  [state] {e.name} = {val}", flush=True)

    cmd = sys.argv[1] if len(sys.argv) > 1 else 'list'
    if cmd == 'list':
        for e in entities:
            print(type(e).__name__, '|', e.name)
    elif cmd == 'press':
        target = sys.argv[2].lower()
        watch = float(sys.argv[3]) if len(sys.argv) > 3 else 12
        btn = next(e for e in entities if type(e).__name__ == 'ButtonInfo' and target in e.name.lower())
        cli.subscribe_states(on_state)
        await asyncio.sleep(1)
        print(f"pressing: {btn.name}", flush=True)
        cli.button_command(btn.key)
        await asyncio.sleep(watch)
    elif cmd == 'number':
        target = sys.argv[2].lower()
        value = float(sys.argv[3])
        watch = float(sys.argv[4]) if len(sys.argv) > 4 else 3
        num = next(e for e in entities if type(e).__name__ == 'NumberInfo' and target in e.name.lower())
        print(f"setting {num.name} = {value}", flush=True)
        cli.number_command(num.key, value)
        cli.subscribe_states(on_state)
        await asyncio.sleep(watch)
    elif cmd == 'watch':
        cli.subscribe_states(on_state)
        await asyncio.sleep(float(sys.argv[2]))
    await cli.disconnect()


if __name__ == '__main__':
    asyncio.run(main())
