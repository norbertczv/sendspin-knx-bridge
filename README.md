# Sendspin KNX Bridge

Home Assistant App that connects to Music Assistant through the official Sendspin
Python client and converts visualizer data (loudness, spectrum, beat and peak)
into RGBW light commands through the Home Assistant Core API.

## v0.2.0

- Uses `aiosendspin` instead of implementing the Noise handshake manually.
- Removes the direct `xknx` dependency that caused the v0.1 startup failure.
- Uses Home Assistant's already-working KNX light entities.
- Controls both mood-light entities in one HA service call.
- Rate-limits light updates to protect the KNX bus.
- Starts in **dry-run mode** for the first validation.
- Persists the Sendspin client identity and pairing store in `/data`.

Default installation values are prepared for this system:
- Sendspin: `ws://192.168.1.201:8927/sendspin`
- Lights: `light.rejtett_vilagitas,light.hangulat_vilagitas`
- Output rate: 4 Hz

After updating the App, leave `dry_run: true` for the first start and inspect the log.
