# Changelog

## 1.1.0 – 2026-11-05

- Redesigned web UI (shared homelab theme, light/dark): header with tabs and theme toggle, overview with a
  status hero card and KPI tiles, room model as room cards with LQI meters, devices table with state pills.
  Also applies to the self-contained weekly report. Works on phones (375 px).
- Wi-Fi channel history + best-channel hint: every Wi-Fi channel reading is stored (new table `wifi_readings`,
  schema version 2, migrated automatically). The channel card shows the current state, the number of Wi-Fi channel
  changes in the last 7 days and the share of time spent overlapping. Two or more changes are reported as
  automatic channel selection; on overlap or auto-hopping the card lists the best Wi-Fi channels for the Zigbee
  channel and says which fixed channel to set in the router. New metric `zpa_wifi_channel_changes_7d`.
- Wi-Fi channel source: new `wifi.timeout_seconds` (default 45, was a fixed 10 s; fritz_exporter can need
  10-30 s per scrape), and timeouts are named in the incident text instead of an empty message.
- Optional dashboard link: `ui.home_url` (default empty = no button) and `ui.home_label` (default "Dashboard").

## 1.0.0

- First release.
