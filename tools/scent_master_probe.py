#!/usr/bin/env python3
"""Standalone bench test for Scent Master Pro diffusers.

Talks to the diffuser over plain BLE (bleak) so the protocol can be checked
against real hardware without touching Home Assistant. Only bleak is needed:

    python3 -m pip install bleak
    python3 tools/scent_master_probe.py                 # read-only
    python3 tools/scent_master_probe.py --write-test 2  # rewrite Setting 2 unchanged

Frames and decoding follow PROTOCOL.md; `--selftest` checks the encoder against
the captured frames without any radio.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta
import os
import re
import sys

SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000ffe2-0000-1000-8000-00805f9b34fb"

HEADER = 0x55
DIR_WRITE = 0x10
DIR_NOTIFY = 0x11
FN_READ = 0x00
FN_DATA = 0x05

REG_CLOCK = 0x00
REG_SCHEDULE_1 = 0x01
REG_FAN = 0x06
REG_SESSION = 0x07
REG_ENABLE_1 = 0x11
REG_STATUS = 0xD1
REG_MODE = 0xD2
REG_NAME = 0xDA
REG_NOTE = 0xDB

SLOTS = 5
RECORD = 10
DAYS = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40)  # Mon..Sun
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MAC_RE = re.compile(r"^(?:[0-9A-F]{2}[:-]){2,5}[0-9A-F]{2}$|^(?:[0-9A-F]{2}){3,6}$", re.I)


# -- framing -----------------------------------------------------------------

def frame(cmd: int, payload: bytes | None = None) -> bytes:
    """`55 10 <cmd> <fn> [<len> <payload>] <chk>`, chk = sum after the 0x55."""
    body = bytearray([DIR_WRITE, cmd & 0xFF])
    if payload is None:
        body.append(FN_READ)
    else:
        body.append(FN_DATA)
        body.append(len(payload) & 0xFF)
        body.extend(payload)
    body.append(sum(body) & 0xFF)
    return bytes([HEADER]) + bytes(body)


def read(cmd: int) -> bytes:
    return frame(cmd)


def write(cmd: int, payload: bytes) -> bytes:
    return frame(cmd, payload)


def clock_frame(now: datetime | None = None) -> bytes:
    now = now or datetime.now()
    return write(REG_CLOCK, bytes([DAYS[now.weekday()], now.hour, now.minute, now.second]))


def record_payload(weekdays: int, start: tuple[int, int], end: tuple[int, int],
                   work: int, pause: int) -> bytes:
    return bytes([
        weekdays & 0x7F,
        start[0] & 0xFF, start[1] & 0xFF, end[0] & 0xFF, end[1] & 0xFF,
        (work >> 8) & 0xFF, work & 0xFF,
        (pause >> 8) & 0xFF, pause & 0xFF,
    ])


def schedule_frame(slot: int, payload: bytes) -> bytes:
    return write(REG_SCHEDULE_1 + slot - 1, payload)


def enable_frame(slot: int, on: bool) -> bytes:
    return write(REG_ENABLE_1 + slot - 1, bytes([0x01 if on else 0x00]))


def payload_from(rec: dict) -> bytes:
    """Rebuild a slot's 9-byte record from the decoded form."""
    sh, sm = (int(x) for x in rec["start"].split(":"))
    eh, em = (int(x) for x in rec["end"].split(":"))
    return record_payload(rec["weekdays"], (sh, sm), (eh, em), rec["work"], rec["pause"])


def is_empty(rec: dict) -> bool:
    return not (rec["weekdays"] or rec["work"] or rec["pause"]) and \
        rec["start"] == "00:00" and rec["end"] == "00:00"


def spray_window(now: datetime, lead: int,
                 length: int = 2) -> tuple[tuple[int, int], tuple[int, int]]:
    """Start/end of a short window: `lead` minutes from now, `length` long."""
    start_total = (now.hour * 60 + now.minute + lead) % (24 * 60)
    end_total = min(start_total + max(1, length), 23 * 60 + 59)
    return divmod(start_total, 60), divmod(end_total, 60)


# -- decoding ----------------------------------------------------------------

def parse(argv: bytes) -> tuple[int, bytes] | None:
    if len(argv) < 5 or argv[0] != HEADER or argv[1] != DIR_NOTIFY:
        return None
    return argv[2], argv[5:5 + argv[4]]


def decode_schedules(payload: bytes) -> list[dict] | None:
    if len(payload) < SLOTS * RECORD:
        return None
    out = []
    for i in range(SLOTS):
        g = payload[i * RECORD:(i + 1) * RECORD]
        out.append({
            "slot": i + 1,
            "enabled": g[0] == 0x01,
            "weekdays": g[1] & 0x7F,
            "start": f"{g[2]:02d}:{g[3]:02d}",
            "end": f"{g[4]:02d}:{g[5]:02d}",
            "work": (g[6] << 8) | g[7],
            "pause": (g[8] << 8) | g[9],
        })
    return out


def describe(schedules: list[dict]) -> str:
    lines = []
    for s in schedules:
        days = [DAY_NAMES[i] for i in range(7) if s["weekdays"] & DAYS[i]]
        lines.append(
            f"  Setting {s['slot']}: {'ON ' if s['enabled'] else 'off'} "
            f"{s['start']}-{s['end']}  work {s['work']}s pause {s['pause']}s  "
            f"{','.join(days) or 'no days'}"
        )
    return "\n".join(lines)


# -- radio -------------------------------------------------------------------

class Link:
    def __init__(self, client) -> None:
        self.client = client
        self.frames: list[bytes] = []

    def _on_notify(self, _char, data: bytearray) -> None:
        self.frames.append(bytes(data))
        print(f"  <- {bytes(data).hex(' ').upper()}")

    async def start(self) -> None:
        await self.client.start_notify(NOTIFY_UUID, self._on_notify)

    async def send(self, data: bytes, settle: float = 0.35) -> None:
        print(f"  -> {data.hex(' ').upper()}")
        await self.client.write_gatt_char(WRITE_UUID, data, response=False)
        await asyncio.sleep(settle)

    async def wait_for(self, cmd: int, timeout: float = 3.0) -> bytes | None:
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            for f in list(self.frames):
                got = parse(f)
                if got and got[0] == cmd:
                    self.frames.remove(f)
                    return got[1]
            await asyncio.sleep(0.05)
        return None

    async def read_schedules(self, timeout: float = 4.0) -> list[dict] | None:
        """D1 is answered with D1 or F2 depending on firmware."""
        await self.send(read(REG_STATUS))
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            for f in list(self.frames):
                got = parse(f)
                if got and got[0] in (REG_STATUS, 0xF2):
                    self.frames.remove(f)
                    schedules = decode_schedules(got[1])
                    if schedules:
                        return schedules
            await asyncio.sleep(0.05)
        return None


def mfr_bytes(adv) -> bytes:
    data = getattr(adv, "manufacturer_data", None) or {}
    return b"".join(bytes(v) for v in data.values())


def mac_prefix(value: str) -> bytes:
    digits = re.sub(r"[^0-9A-Fa-f]", "", value)
    return bytes.fromhex(digits) if digits and len(digits) % 2 == 0 else b""


async def scan(timeout: float):
    from bleak import BleakScanner

    print(f"scanning {timeout:.0f}s…")
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    return list(found.values())


def report(found) -> None:
    print(f"{len(found)} device(s) advertising")
    for dev, adv in found:
        mfr = mfr_bytes(adv)
        services = [str(u)[4:8] for u in (getattr(adv, "service_uuids", None) or [])]
        name = dev.name or getattr(adv, "local_name", "") or "(no name)"
        print(
            f"  {name:34s} {dev.address}  rssi {getattr(adv, 'rssi', 0):4d}  "
            f"mfr {mfr[:10].hex(' ').upper() if mfr else '-'}  svc {services}"
        )


def pick(found, address: str | None, name_filter: str):
    """Name fragment, address/UUID fragment, or the MAC in the manufacturer data."""
    want = mac_prefix(address) if address and MAC_RE.match(address.strip()) else b""
    for dev, adv in found:
        label = f"{dev.name or ''} {getattr(adv, 'local_name', '') or ''}"
        if address and address.upper() in f"{label} {dev.address}".upper():
            return dev
        if want and mfr_bytes(adv).upper().startswith(want.upper()):
            return dev
        if not address and name_filter and name_filter.upper() in label.upper():
            return dev
    return None


async def run(args) -> int:
    from bleak import BleakClient

    found = await scan(args.timeout)
    report(found)
    if args.list:
        return 0
    target = pick(found, args.address, args.name)
    if target is None:
        print("no matching device. Close the phone app, keep the unit powered and in range,")
        print("and give Terminal Bluetooth permission (System Settings > Privacy & Security).")
        return 2
    print(f"connecting to {target.name or '(no name)'} [{target.address}]")
    async with BleakClient(target, timeout=30.0) as client:
        link = Link(client)
        await link.start()
        print("session start:")
        await link.send(write(REG_SESSION, bytes([0x01])))
        print("clock:")
        await link.send(clock_frame())
        print("read schedules:")
        schedules = await link.read_schedules()
        if not schedules:
            print("no schedule block — the unit did not answer 0xD1 (or answered a shape we don't know)")
            return 3
        print(describe(schedules))

        for reg, label in ((REG_FAN, "level/off"), (REG_MODE, "mode"),
                           (REG_NAME, "name"), (REG_NOTE, "note")):
            await link.send(read(reg))
            payload = await link.wait_for(reg, timeout=1.5)
            if payload is None:
                print(f"  {label}: no answer")
            elif reg in (REG_NAME, REG_NOTE):
                print(f"  {label}: {payload.strip(bytes(1)).decode('utf-8', 'replace')!r}")
            else:
                print(f"  {label}: {payload.hex(' ').upper()}")

        if args.pulse:
            return await pulse(link, schedules, args)

        if args.spray is not None:
            return await spray(link, schedules, args)

        if args.write_test:
            slot = args.write_test
            current = schedules[slot - 1]
            sh, sm = (int(x) for x in current["start"].split(":"))
            eh, em = (int(x) for x in current["end"].split(":"))
            start, end = (sh, sm), (eh, em)
            payload = record_payload(
                current["weekdays"], start, end, current["work"], current["pause"]
            )
            print(f"writing Setting {slot} back unchanged ({payload.hex(' ').upper()}):")
            await link.send(schedule_frame(slot, payload))
            await link.send(enable_frame(slot, current["enabled"]))
            after = await link.read_schedules()
            if after is None:
                print("read-back failed")
                return 4
            print(describe(after))
            ok = after[slot - 1] == current
            print(f"write-back {'confirmed' if ok else 'DID NOT stick'}")
            if not ok:
                return 5
    return 0


async def listen_for_puff(until: datetime, grace: float, label: str) -> float | None:
    """Heartbeat every 5 s and return the loop time Enter was pressed, if any.

    Arrow keys only queue bytes, so the wait ends on a real newline.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, (until - datetime.now()).total_seconds()) + grace
    pressed: list[float] = []
    fd: int | None = None
    if sys.stdin.isatty():
        ifd: int = sys.stdin.fileno()
        fd = ifd

        def _readable() -> None:
            try:
                chunk = os.read(ifd, 4096)
            except OSError:
                chunk = b""
            if (b"\n" in chunk or b"\r" in chunk) and not pressed:
                pressed.append(loop.time())

        loop.add_reader(ifd, _readable)
    try:
        while loop.time() < deadline and not pressed:
            print(f"  [{datetime.now():%H:%M:%S}] {label}")
            await asyncio.sleep(min(5.0, max(0.0, deadline - loop.time())))
    finally:
        if fd is not None:
            loop.remove_reader(fd)
    return pressed[0] if pressed else None


def _report_puff(hit: float | None, t0: float) -> None:
    if hit is None:
        print("  sin Enter: no se marcó ningún puf")
    else:
        print(f"  puf marcado {hit - t0:.0f}s después de escribir")


async def spray(link: Link, schedules: list[dict], args) -> int:
    """One-shot cycle through a temporary window; times it against the puff."""
    slot = args.spray
    if not slot:
        free = [s for s in schedules if not s["enabled"] and is_empty(s)]
        if not free:
            print("no free slot: clear one in the app, or pass --spray SLOT to reuse one")
            return 6
        slot = free[0]["slot"]

    original = schedules[slot - 1]
    now = datetime.now()
    start, end = spray_window(now, args.lead, args.window)
    payload = record_payload(DAYS[now.weekday()], start, end, args.work, args.pause)
    closes = now.replace(second=0, microsecond=0) + timedelta(minutes=args.lead + args.window)

    state = "ON" if original["enabled"] else "off"
    print(f"\nSetting {slot} is {state}; writing a one-shot window "
          f"{start[0]:02d}:{start[1]:02d}-{end[0]:02d}:{end[1]:02d} "
          f"({DAY_NAMES[now.weekday()]}, work {args.work}s, pause {args.pause}s)")
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    await link.send(schedule_frame(slot, payload), settle=0.2)
    await link.send(enable_frame(slot, True), settle=0.2)

    label = f"ventana {start[0]:02d}:{start[1]:02d}-{end[0]:02d}:{end[1]:02d} abierta"
    print("pulsa Enter al oír el puf; si no llega, sigo escuchando "
          f"{args.grace:.0f}s tras el cierre")
    _report_puff(await listen_for_puff(closes, args.grace, label), t0)

    if args.keep:
        print(f"dejo Setting {slot} con la ventana temporal")
        return 0
    print("restaurando el slot (aviso: en la prueba anterior el puf sonó aquí)")
    t1 = loop.time()
    await link.send(schedule_frame(slot, payload_from(original)), settle=0.2)
    await link.send(enable_frame(slot, original["enabled"]), settle=0.2)
    _report_puff(await listen_for_puff(datetime.now(), 10, "restauración enviada"), t1)
    after = await link.read_schedules()
    if after is None:
        print("restore sent, read-back failed")
        return 4
    print(describe(after))
    print(f"Setting {slot} {'restored' if after[slot - 1] == original else 'NOT restored'}")
    return 0 if after[slot - 1] == original else 5


async def pulse(link: Link, schedules: list[dict], args) -> int:
    """Write the enable register in steps, to see which value puffs."""
    slot = args.pulse
    original = schedules[slot - 1]
    loop = asyncio.get_event_loop()
    print(f"\nSetting {slot} is {'ON' if original['enabled'] else 'off'}; "
          "only the enable register is written, the record stays untouched")

    for step, value in enumerate((0, 0, 1, 0), start=1):
        print(f"paso {step}: enable = {value}")
        t = loop.time()
        await link.send(enable_frame(slot, bool(value)), settle=0.2)
        _report_puff(await listen_for_puff(datetime.now(), 12,
                                          f"paso {step} enable={value}"), t)

    await link.send(enable_frame(slot, original["enabled"]), settle=0.2)
    after = await link.read_schedules()
    if after is None:
        return 4
    print(describe(after))
    ok = after[slot - 1] == original
    print(f"Setting {slot} {'restored' if ok else 'NOT restored'}")
    return 0 if ok else 5


def selftest() -> int:
    checks = {
        read(REG_STATUS): "55 10 D1 00 E1",
        read(REG_NOTE): "55 10 DB 00 EB",
        read(REG_MODE): "55 10 D2 00 E2",
        clock_frame(datetime(2026, 11, 22, 14, 31, 50)): "55 10 00 05 04 40 0E 1F 32 B8",
        schedule_frame(2, record_payload(0x52, (11, 0), (19, 0), 5, 900)):
            "55 10 02 05 09 52 0B 00 13 00 00 05 03 84 1C",
        schedule_frame(2, record_payload(0x52, (9, 0), (19, 0), 5, 900)):
            "55 10 02 05 09 52 09 00 13 00 00 05 03 84 1A",
        enable_frame(2, True): "55 10 12 05 01 01 29",
        write(REG_SESSION, b"\x01"): "55 10 07 05 01 01 1E",
    }
    bad = 0
    for got, expected in checks.items():
        want = bytes.fromhex(expected.replace(" ", ""))
        status = "ok" if got == want else f"MISMATCH want {expected}"
        bad += got != want
        print(f"{got.hex(' ').upper():60s} {status}")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--address", help="BLE address or part of it (e.g. 9D:ED:F1)")
    ap.add_argument("--name", default="-TJ-", help="name fragment to match (default -TJ-)")
    ap.add_argument("--timeout", type=float, default=20.0, help="scan seconds (default 20)")
    ap.add_argument("--list", action="store_true", help="only list devices, do not connect")
    ap.add_argument("--write-test", type=int, metavar="SLOT",
                    help="rewrite that schedule unchanged and confirm the read-back")
    ap.add_argument("--spray", type=int, nargs="?", const=0, metavar="SLOT",
                    help="time a one-shot cycle written into a free slot (or SLOT)")
    ap.add_argument("--lead", type=int, default=1,
                    help="minutes until the temporary window starts (default 1, 0 = this minute)")
    ap.add_argument("--window", type=int, default=2,
                    help="length of the temporary window in minutes (default 2)")
    ap.add_argument("--work", type=int, default=5, help="spray seconds (default 5)")
    ap.add_argument("--pause", type=int, default=900, help="pause seconds (default 900)")
    ap.add_argument("--grace", type=float, default=90.0,
                    help="seconds to keep listening after the window closes (default 90)")
    ap.add_argument("--pulse", type=int, metavar="SLOT",
                    help="write only SLOT's enable register as 0,0,1,0 to find the spray edge")
    ap.add_argument("--keep", action="store_true",
                    help="leave the temporary window in the slot instead of restoring it")
    ap.add_argument("--selftest", action="store_true", help="check frames offline")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
