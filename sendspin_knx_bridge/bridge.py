#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import base64
import colorsys
import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Iterable

import aiohttp

from aiosendspin.client import SendspinClient
from aiosendspin.models.types import Roles
from aiosendspin.models.visualizer import (
    ClientHelloVisualizerSpectrum,
    ClientHelloVisualizerSupport,
    VisualizerFrame,
)
from aiosendspin.noise.keys import Identity
from aiosendspin.noise.trust_store import FileClientPairingStore

OPTIONS_FILE = Path("/data/options.json")
IDENTITY_FILE = Path("/data/sendspin_identity.bin")
PAIRING_FILE = Path("/data/sendspin_pairing.json")
HA_API = "http://supervisor/core/api"


def read_options() -> dict:
    with OPTIONS_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


CFG = read_options()
LOG_LEVEL = str(CFG.get("log_level", "INFO")).upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
)
LOG = logging.getLogger("sendspin_knx_bridge")

SENDSPIN_URL = str(CFG["sendspin_url"])
LIGHT_ENTITIES = [
    x.strip() for x in str(CFG["light_entities"]).split(",") if x.strip()
]
VIS_RATE = max(1, min(30, int(CFG.get("visualizer_rate_hz", 8))))
OUTPUT_RATE = max(1, min(10, int(CFG.get("output_rate_hz", 4))))
BMIN = max(1, min(100, int(CFG.get("brightness_min", 12))))
BMAX = max(BMIN, min(100, int(CFG.get("brightness_max", 65))))
SMIN = max(0, min(100, int(CFG.get("saturation_min", 70))))
SMAX = max(SMIN, min(100, int(CFG.get("saturation_max", 95))))
BEAT_BOOST = max(0, min(40, int(CFG.get("beat_boost", 12))))
SPECTRUM_BINS = max(4, min(32, int(CFG.get("spectrum_bins", 12))))
COLOR_CYCLE_HELPER = str(CFG.get("disable_color_cycle_helper", "")).strip()
DRY_RUN = bool(CFG.get("dry_run", True))
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")

if not LIGHT_ENTITIES:
    raise RuntimeError("No light_entities configured")
if not TOKEN and not DRY_RUN:
    raise RuntimeError("SUPERVISOR_TOKEN is missing; Home Assistant API is unavailable")


class VisualState:
    def __init__(self) -> None:
        self.loudness = 0
        self.spectrum: list[int] | None = None
        self.f_peak_freq = 0
        self.f_peak_amp = 0
        self.beat_until = 0.0
        self.peak_strength = 0
        self.peak_until = 0.0
        self.last_frame = 0.0
        self.changed = asyncio.Event()
        self.helper_disabled = False

    def apply(self, frames: Iterable[VisualizerFrame]) -> None:
        now = time.monotonic()
        got = False
        for frame in frames:
            got = True
            if frame.loudness is not None:
                self.loudness = int(frame.loudness)
            if frame.spectrum is not None:
                self.spectrum = list(frame.spectrum)
            if frame.f_peak_freq is not None:
                self.f_peak_freq = int(frame.f_peak_freq)
            if frame.f_peak_amp is not None:
                self.f_peak_amp = int(frame.f_peak_amp)
            if frame.is_downbeat is not None:
                # A beat frame carries is_downbeat False for an ordinary beat,
                # True for a bar-start beat. Presence of the field means beat.
                self.beat_until = now + 0.22
            if frame.peak_strength is not None:
                self.peak_strength = int(frame.peak_strength)
                self.peak_until = now + 0.18
        if got:
            self.last_frame = now
            self.changed.set()


def load_or_create_identity() -> Identity:
    if IDENTITY_FILE.exists():
        raw = IDENTITY_FILE.read_bytes()
        if len(raw) == 32:
            return Identity.from_private_bytes(raw)
        LOG.warning("Stored Sendspin identity was invalid; generating a new one")
    ident = Identity.generate()
    IDENTITY_FILE.write_bytes(ident.private_bytes)
    os.chmod(IDENTITY_FILE, 0o600)
    return ident


def spectral_hue(state: VisualState) -> float:
    """Map spectral centre of gravity to an attractive 0..360 hue."""
    bins = state.spectrum
    if bins and any(bins):
        total = float(sum(bins))
        centre = sum(i * v for i, v in enumerate(bins)) / total
        norm = centre / max(1, len(bins) - 1)
        # Bass -> warm amber/red, mids -> green/cyan, highs -> blue/purple.
        return (18.0 + 275.0 * (norm ** 0.82)) % 360.0

    if state.f_peak_freq > 0:
        f = max(50.0, min(16000.0, float(state.f_peak_freq)))
        norm = math.log10(f / 50.0) / math.log10(16000.0 / 50.0)
        return (18.0 + 275.0 * norm) % 360.0

    return 200.0


def compute_light(state: VisualState) -> tuple[tuple[int, int, int, int], int, float, float]:
    now = time.monotonic()
    loud = max(0.0, min(1.0, state.loudness / 65535.0))

    # dB-scaled loudness benefits from a gentle curve for room lighting.
    energy = math.sqrt(loud)
    brightness = BMIN + (BMAX - BMIN) * energy

    beat = now < state.beat_until
    peak = now < state.peak_until
    if beat:
        brightness += BEAT_BOOST
    if peak:
        brightness += BEAT_BOOST * 0.55 * max(0.15, state.peak_strength / 255.0)
    brightness = int(round(max(BMIN, min(BMAX, brightness))))

    hue = spectral_hue(state)
    spectral_activity = loud
    if state.spectrum and any(state.spectrum):
        spectral_activity = max(spectral_activity, max(state.spectrum) / 65535.0)
    saturation = SMIN + (SMAX - SMIN) * math.sqrt(max(0.0, min(1.0, spectral_activity)))
    saturation = max(SMIN, min(SMAX, saturation))

    r, g, b = colorsys.hsv_to_rgb(hue / 360.0, saturation / 100.0, 1.0)
    # A little RGBW white keeps the KNX strips pleasant without washing out colour.
    white = int(round(255 * max(0.0, (100.0 - saturation) / 100.0) * 0.35))
    rgbw = (
        int(round(r * 255)),
        int(round(g * 255)),
        int(round(b * 255)),
        white,
    )
    return rgbw, brightness, hue, saturation


async def ha_service(session: aiohttp.ClientSession, domain: str, service: str, data: dict) -> None:
    url = f"{HA_API}/services/{domain}/{service}"
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    }
    async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=5)) as resp:
        body = await resp.text()
        if resp.status >= 300:
            raise RuntimeError(f"HA {domain}.{service} failed: HTTP {resp.status}: {body[:300]}")


async def disable_competing_cycle(session: aiohttp.ClientSession, state: VisualState) -> None:
    if not COLOR_CYCLE_HELPER or state.helper_disabled or DRY_RUN:
        return
    await ha_service(
        session,
        "input_boolean",
        "turn_off",
        {"entity_id": COLOR_CYCLE_HELPER},
    )
    state.helper_disabled = True
    LOG.info("Disabled competing colour-cycle helper: %s", COLOR_CYCLE_HELPER)


async def output_loop(session: aiohttp.ClientSession, state: VisualState) -> None:
    min_interval = 1.0 / OUTPUT_RATE
    last_sent = 0.0
    last_tuple: tuple | None = None

    while True:
        await state.changed.wait()
        state.changed.clear()

        delay = min_interval - (time.monotonic() - last_sent)
        if delay > 0:
            await asyncio.sleep(delay)

        # Ignore stale state after visualizer traffic stops.
        if time.monotonic() - state.last_frame > 2.0:
            continue

        rgbw, brightness, hue, saturation = compute_light(state)
        current = (rgbw, brightness)

        # Avoid duplicate KNX/HA traffic if the visible state did not change.
        if current == last_tuple and time.monotonic() - last_sent < 1.0:
            continue

        if DRY_RUN:
            LOG.info(
                "DRY RUN visualizer -> hue=%.1f sat=%.1f%% brightness=%d%% rgbw=%s",
                hue, saturation, brightness, rgbw,
            )
        else:
            await disable_competing_cycle(session, state)
            await ha_service(
                session,
                "light",
                "turn_on",
                {
                    "entity_id": LIGHT_ENTITIES,
                    "rgbw_color": list(rgbw),
                    "brightness_pct": brightness,
                },
            )

        last_tuple = current
        last_sent = time.monotonic()


async def run_once() -> None:
    identity = load_or_create_identity()
    pairing_store = await FileClientPairingStore.open(PAIRING_FILE)

    visualizer_support = ClientHelloVisualizerSupport(
        buffer_capacity=131072,
        rate_max=VIS_RATE,
        types=["loudness", "spectrum", "f_peak", "beat", "peak"],
        spectrum=ClientHelloVisualizerSpectrum(
            n_disp_bins=SPECTRUM_BINS,
            scale="log",
            f_min=50,
            f_max=16000,
        ),
    )

    state = VisualState()
    disconnected = asyncio.Event()

    async with aiohttp.ClientSession() as ha_session:
        client = SendspinClient(
            identity,
            "KNX Mood Lights",
            [Roles.VISUALIZER],
            pairing_store=pairing_store,
            visualizer_support=visualizer_support,
        )

        client.add_visualizer_listener(state.apply)
        client.add_disconnect_listener(disconnected.set)

        output_task = asyncio.create_task(output_loop(ha_session, state))
        try:
            LOG.info("Sendspin KNX Bridge v0.2.0 starting")
            LOG.info("Sendspin URL: %s", SENDSPIN_URL)
            LOG.info("Target lights: %s", ", ".join(LIGHT_ENTITIES))
            LOG.info("Visualizer rate: %d Hz; HA/KNX output cap: %d Hz", VIS_RATE, OUTPUT_RATE)
            LOG.info("Dry-run: %s", DRY_RUN)
            LOG.info("Sendspin client id: %s", identity.peer_id)

            await client.connect(SENDSPIN_URL)
            LOG.info("Sendspin encrypted connection established")
            await client.send_available(available=True)
            LOG.info("Visualizer client marked available; waiting for Music Assistant stream")

            await disconnected.wait()
            raise ConnectionError("Sendspin connection closed")
        finally:
            output_task.cancel()
            try:
                await output_task
            except asyncio.CancelledError:
                pass
            try:
                await client.disconnect()
            except Exception:
                pass


async def main() -> None:
    retry = 3
    while True:
        try:
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception("Bridge connection/session error")
            LOG.info("Retrying in %d seconds", retry)
            await asyncio.sleep(retry)
            retry = min(30, retry + 2)


if __name__ == "__main__":
    asyncio.run(main())
