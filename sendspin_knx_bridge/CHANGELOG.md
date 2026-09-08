# Changelog

## 0.2.0
- Replaced direct xknx calls with Home Assistant Core API light control.
- Added official `aiosendspin` client for current encrypted Sendspin protocol.
- Added persistent Sendspin identity and pairing store.
- Added loudness/spectrum/beat/peak mapping.
- Added 4 Hz default output rate limit.
- Added duplicate-write suppression.
- Added automatic disabling of the existing slow colour-cycle helper.
- Added dry-run mode (enabled by default) for safe first validation.
- Kept old KNX gateway/H/S/V options in the schema for painless upgrade from 0.1;
  they are no longer used by the bridge.
