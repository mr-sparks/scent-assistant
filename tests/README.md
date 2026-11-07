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
