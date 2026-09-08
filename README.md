# Sendspin KNX Bridge

Home Assistant App for Music Assistant Sendspin visualizer → KNX HSV mood lighting.

Initial KNX mapping:
- Hue: 3/5/73
- Saturation: 3/5/74
- Brightness: 3/5/28

The bridge rate-limits KNX writes (default 4 Hz). Sendspin visualizer is currently a technical preview, so the bridge is intentionally tolerant and marked v0.1.0.
