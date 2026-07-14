# Spa WebSocket — Home Assistant integration

A Home Assistant custom integration that controls a spa over its WebSocket
endpoint (`wss://…/ws`). It's a port of the `homebridge-spa-jets` Homebridge
plugin: single-character commands toggle the jets and filter, and incoming
`{"dsp": "<hex>"}` status frames report the jets state.

## What it creates

One WebSocket connection is shared by three entities grouped under a single
device:

| Entity | Type | Behavior |
| --- | --- | --- |
| **Jets** | `button` | Press → sends `"3"` (toggle jets) |
| **Filter** | `button` | Press → sends `"4"` (toggle filter) |
| **Jets status** | `sensor` (enum) | Reports `Off` / `Low` / `High` / `Filtering` |

## Install via HACS (recommended)

1. In Home Assistant, open **HACS**.
2. Top-right **⋮ menu → Custom repositories**.
3. Add `https://github.com/jeremystover/ha-spa`, category **Integration**,
   then **Add**.
4. Search HACS for **Spa WebSocket**, open it, and click **Download**.
5. **Restart Home Assistant** (Settings → System → top-right ⋮ → Restart).
6. **Settings → Devices & services → Add integration → Spa WebSocket**, and
   paste your spa's `wss://…/ws` URL.

## Manual install

Copy `custom_components/spa_websocket` into your Home Assistant config
directory so it lands at `<config>/custom_components/spa_websocket/`, restart,
then add the integration as in step 6 above.

No YAML editing and no extra Python dependencies — the integration uses Home
Assistant's bundled `aiohttp` client.

## How this maps from the Homebridge plugin

- The two momentary "revert-to-off" switches (`SpaJets`, `SpaFilter`) become
  `button` entities. Home Assistant's button domain is stateless by design, so
  the old 0.5-second revert hack is no longer needed — a press just fires once.
- The read-only `Fanv2` accessory (`SpaStatus`) becomes an enum `sensor`. The
  decoding is identical: byte 5 of the `dsp` hex string (`04` = Low,
  `08` = High, `10` = Filtering, else Off).
- The three separate sockets in the plugin are collapsed into one shared,
  auto-reconnecting connection, with the same 5-second reconnect delay.

## Notes

- The status sensor reads `Off` until the first `dsp` frame arrives after
  startup.
- To use the jets state in automations, trigger on the
  `sensor.<name>_jets_status` state (e.g. `to: "High"`).

## License

MIT
