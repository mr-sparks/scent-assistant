# Offline integration tests

Run from the repository root using Python's standard library; no third-party
packages or Home Assistant installation are needed:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -X dev -W error::ResourceWarning -m unittest discover -s tests -t . -v
```

The suite imports complete production device, protocol, sensor, timer entity,
options, diagnostics and integration lifecycle modules. `offline.py` supplies
explicitly minimal HA, Bleak, aiohttp and schema import stubs under a temporary
`sys.modules` patch; it does not extract or copy production methods. Lifecycle
tests use an inert HA/entry boundary rather than registering real services.
BLE/HTTP transport construction and network connections are forbidden. Peers
hold separate synthetic state and deliver framed replies through the production
notification parser and state callbacks.

## Scent Tech timer confirmation

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

## Aroma-Link Stay connected

- The default remains off; only Aroma-Link Bluetooth entries expose/accept the
  boolean. AI-Diffuser keeps its existing refresh-interval option, not this
  reader. Enabling does not connect, and the reader sends only status queries
  with a fixed 20-second sleep, never physical commands or reconnect attempts.
- Both oil sensors are observed through their real callbacks, native values,
  availability and attributes. Clock/countdown-only replies produce no changed
  oil snapshots while raw BLE freshness and remaining-time values advance. An
  actual oil reply, even unchanged, refreshes only that measurement's metadata;
  real oil changes stay visible. Other-family oil timestamps retain their prior
  behavior. This is recorder-facing snapshot comparison, not an HA Recorder DB.
- Expected and unexpected transport errors, dropped links, hung sends,
  unsubscribe timeouts, cancellation and unload release/observe reader work.
  Rapid toggles retain one worker and cannot cancel failure cleanup. Serialized
  teardown/connect races preserve replacement clients. Diagnostics distinguish
  the configured preference, reader lifecycle and connected/subscribed link.
- Setup, initial retry, manual, timed and Refresh-button reads share the real
  Aroma-Link read gate. Delayed week-table fragments (including a delayed first
  header), overlapping reads, timeout/cancellation and stale tails exercise
  completion/cleanup. Reverse ordering protects an in-flight short reply from
  a new full read. Full subqueries also wait for a prior fragmented reply.
- Disabling restores the existing ten-second idle disconnect. Reader failures
  respect the existing connect cooldown; only a normal refresh reconnects.

The Aroma-Link peer implements an inert GATT boundary; the actual production
`_ble_send`, parser and lifecycle execute. Reader and idle sleeps are manually
stepped with their requested production durations asserted; refresh settle
sleeps yield without waiting, and bounded reply/transport timeouts are shortened.
These tests do not establish real firmware latency, proxy operation or Home
Assistant's full configuration/Recorder engine behavior.
