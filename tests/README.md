# Offline timer tests

Run from the repository root using Python's standard library; no third-party
packages or Home Assistant installation are needed:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -X dev -W error::ResourceWarning -m unittest discover -s tests -t . -v
```

The suite imports the complete production device, protocol and timer entity
modules. `offline.py` supplies explicitly minimal HA, Bleak and aiohttp import
stubs under a temporary `sys.modules` patch. It bypasses only the integration's
service-registration `__init__.py`; it does not extract or copy production
methods. BLE/HTTP transport construction and network connections are forbidden.
The peer holds a separate synthetic device table and delivers framed replies
through the production notification parser and state callbacks.

## Contract covered

- A write succeeds only when a new read-back contains the complete expected
  target record, including all unchanged fields, serial and timer ID.
- ACK with missing, absent-target or mismatched read-back fails; missing ACK
  with an exact read-back succeeds. A matching old cache is not confirmation.
- Only observed tables reach the cache: missing replies retain the last read
  values; mismatched replies remain visible as actually read, not rolled back
  or replaced by requested values. There is no automatic write retry.
- Read-modify-write preserves identity and the other slots. The existing shared
  timer-write lock serializes edits to both the same and different slots.
- Transport errors keep the existing teardown path. Unexpected exceptions and
  cancellation propagate, release locks and do not leave event-wait tasks.
- All timer entity setters (duration, time, weekdays, enable and disable) surface
  a `False` result as `HomeAssistantError`; conversions and successful results
  are tested through the actual entity methods too.

The ACK/table timeout is shortened for tests; event waits and the existing
post-write settle delay are real asyncio operations. These tests do not prove
HA service dispatch, translations, BLE/proxy behavior, firmware timing or a
physical device write. No live credentials, logs or captures are used.

## Aroma-Link phase and countdown freshness

The same command exercises expiry with fabricated Aroma-Link Bluetooth frames
fed into the production parser and entity callbacks. Only a received phase
makes the phase current, and an active countdown needs its own field in that
same report. A true zero is still a valid snapshot. Unrelated packets, partial
packets, optimistic commands, and the mere passage of time do not establish
freshness.

Phase expires after 660 seconds by default (300-second timed refresh).
The freshness window is the greater of 660 seconds or twice the configured
refresh interval plus 60 seconds, so healthy longer intervals do not cause
premature expiry. Countdown still expires after 10 seconds and is not
decremented locally. With reports farther apart (including a 20-second
held-link cadence), countdown can intentionally become unavailable between
reports. Expiry only republishes cached state locally; it does not change
polling, connect, write settings, or start a new read. The synthetic suite
does not validate physical timing or an installed Home Assistant.
