# Zigbee Placement Advisor

Watches the link quality of a Zigbee network (Home Assistant **ZHA**) over weeks and tells you **where an
additional router device (for example a Zigbee smart plug) would help**, e.g.

> A router device in Hallway would probably stabilise 4 weak device(s) (confidence: medium).
> Data basis: 21.0 days, 4,812 measurements, 126 neighbor-table scans, 14 devices.

It **observes and recommends only**. It never changes the ZHA configuration, never changes the Zigbee
channel and never triggers network scans. Every Home Assistant command it sends is read-only (the test suite
checks this).

## Features

- Collects ZHA devices with neighbor tables (LQI, relationship, depth, parent), device type (router / end
  device), power source and Home Assistant area (= room).
- Detects outages ("unavailable") from the Home Assistant recorder history and tells real device outages
  apart from Home Assistant/ZHA restarts (all devices at once).
- Flags weak devices: low median LQI, frequent outages or frequent parent changes (all thresholds configurable).
- Recommends the room for a new router, with a confidence level and the data it is based on. With too little
  data or too few devices it says so instead of guessing.
- Detects a newly added router and automatically rates after X days whether it helped (before/after).
- Checks the Zigbee channel against the 2.4 GHz Wi-Fi channel (via a running
  [fritz_exporter](https://github.com/pdreker/fritz_exporter), or entered manually) and warns on overlap.
- Web UI: room view, devices, LQI history per device, one-time room setup by clicking.
- Weekly HTML report + short Home Assistant notification.
- Prometheus metrics + Grafana dashboard (`grafana/zigbee-placement-advisor.json`).
- Self-monitoring: if a Home Assistant/ZHA update changes the API, the token stops working, HA is unreachable
  or no device reports anything any more, you get a notification instead of silent failure.
- SQLite storage with automatic aggregation and retention; Docker Compose with restart policy, resource
  limits and log rotation; English and German UI.

## How it works

### Data collection and the polling interval

ZHA (zigpy) keeps the neighbor tables of all routers in memory and **rescans them itself every 4 hours**
(`topology_scan_period`). Per-device LQI, RSSI and `last_seen` update with every frame the coordinator receives.
The advisor reads this cache via the WebSocket command `zha/devices` every **30 minutes** (configurable,
5–1440). Reading the cache causes **no radio traffic**; the advisor never sends `zha/topology/update`.
30 minutes is enough to catch every topology scan and regular device traffic without filling the database:

- a device sample is stored only if the device sent a frame since the last poll (`last_seen` changed), so a stale
  LQI is never counted twice;
- a neighbor table is stored only when it changed (= a new scan).

Outages come from `history/history_during_period` (recorder) for one primary entity per device, read
incrementally each cycle (10 days backfill on first start). Devices whose entities are not recorded fall back to
the `available` flag of each poll.

### Weak devices

Over the analysis window (default 14 days) a device is *weak* if its median LQI is below `weak_lqi_median`
(80), or it had at least `weak_outages_per_week` (2) real outages per week, or at least
`weak_parent_changes_per_week` (3) parent changes per week. Outages where ≥ 80 % of all devices drop within
2 minutes count as a system event (HA/ZHA restart, coordinator) and are not held against any device.

Routers with median LQI ≥ `anchor_min_lqi` (150) and the coordinator are *anchors*: good places a new router
can connect to.

### Recommendation

Rooms and which rooms border each other form a graph (set once in the web UI). For a weak device *w* and a
candidate room *c*:

| factor | meaning |
|---|---|
| reach | 1.0 if *c* is *w*'s room, 0.8 if it borders it, otherwise 0 |
| uplink | best anchor quality × the same distance factor (the new router needs a good link itself) |
| between | 1.0 if *c* lies on a shortest path between *w* and its nearest anchor, 0.6 for a detour |
| improvement | *c* must be closer to *w* than every existing anchor |

`p = reach × uplink × between`; *w* counts as stabilised by *c* if `p ≥ 0.5`. Rooms are ranked by how many weak
devices they stabilise.

Weak devices that already share a room with an anchor are reported separately: distance is probably not the
cause (interference, metal, the device itself), so another router would not help.

**Confidence** = data factor (days / 21) × clarity of the winner (margin to the runner-up) × link evidence
(measured neighbor links between the weak devices and that area) × average `p`; ≥ 0.7 high, ≥ 0.45 medium,
otherwise low. This is an estimate from room adjacency and measured link quality, not a radio simulation, and
the report always says so.

No recommendation is made (status "not enough data") with fewer than `min_days` (7) days, fewer than
`min_measurements` (100) measurements or fewer than `min_devices` (5) devices including the coordinator.

### Before/after

A router that joins after the first 24 hours of observation is recorded together with the room recommended at
that moment. After `settle_hours` (24) + `after_days` (7) the advisor compares every device that was weak in
the `before_days` (14) before with the period after: *worked*, *partly worked*, *no effect*, *worse* or
*not enough data*, and whether it was placed in the recommended room. The result is notified and shown in the
UI and the report.

## Requirements

- Home Assistant with the ZHA integration, reachable from the Docker host
- Docker with Compose v2 (amd64 or arm64, e.g. Raspberry Pi 4/5 with 64-bit OS)
- Optional: Prometheus + Grafana, fritz_exporter (for the Wi-Fi channel)

## Installation

```bash
git clone https://github.com/Zedas030/zigbee-placement-advisor.git
cd zigbee-placement-advisor
mkdir -p config data
cp config.example.yaml config/config.yaml   # adjust: language, timezone, notify.services, web.public_url
cp .env.example .env && chmod 600 .env       # put the token in, see "Secrets"
sudo chown 10001:10001 data                  # the container runs as uid 10001
docker build --target test .                 # optional: run the test suite on this machine
docker compose up -d --build
docker compose exec zigbee-placement-advisor python -m zpa check   # one read-only test collection
```

Open `http://<host>:8099/setup`, confirm the rooms, the coordinator's room and the neighbouring rooms.

The container uses host networking, so `http://localhost:8123` is Home Assistant on the same host. Change
`web.port` if 8099 is taken.

## Secrets

| secret | where | how to change / rotate |
|---|---|---|
| Home Assistant long-lived access token | `.env` → `HA_TOKEN=...` (file mode 600, never in `config.yaml`, never committed: `.env` is in `.gitignore`) | In Home Assistant: *Profile → Security → Long-lived access tokens → Create token*. Put the new value in `.env`, run `docker compose up -d` (recreates the container with the new environment), check `http://<host>:8099/healthz`, then delete the old token in Home Assistant. |

The token needs to be able to read ZHA, the registries and the history and to call `notify.*`. Home Assistant
has no per-scope tokens; use a dedicated user if you want to separate it from your admin account.

No other secrets are needed: the Wi-Fi channel is read from fritz_exporter's metrics (fritz_exporter holds the
router credentials), or entered manually (`wifi.source: manual`).

If the token is rejected, the advisor raises an incident ("Home Assistant rejects the token"); it can only be
delivered once a working token is configured, so also watch the Grafana panel *Last collection*.

## Configuration

All options with defaults and explanations: [`config.example.yaml`](config.example.yaml). Unknown keys and
invalid values stop the start with a clear error message (`docker compose logs`).

## Web UI

| path | content |
|---|---|
| `/` | recommendation, room view, channel check, collection health, before/after |
| `/devices` | all devices with LQI median, outages, parent changes, trend |
| `/device/<ieee>?days=30` | LQI history (7/30/90/365 days; > raw retention from daily medians), outages, neighbor links |
| `/setup` | room setup: click a room, then its neighbours (works without JavaScript too) |
| `/reports` | weekly reports, "create report now" |
| `/metrics` | Prometheus metrics |
| `/healthz` | container health (JSON) |
| `/api/state` | current state as JSON |

The UI has no login; run it in a trusted LAN only, or put it behind a reverse proxy with authentication.

## Notifications

Configured in `notify.services` (names without `notify.`, e.g. `mobile_app_phone`). Sent: the weekly summary
(with a link to the HTML report), self-monitoring problems (once when they start, once when resolved), the
before/after result and a channel-overlap warning. If Home Assistant itself is down, messages are queued and
delivered when it answers again.

## Prometheus and Grafana

Add [`prometheus-scrape.yml`](prometheus-scrape.yml) to `scrape_configs`, reload Prometheus, then import
`grafana/zigbee-placement-advisor.json` (or provision it from a dashboards folder). The dashboard asks for the
Prometheus data source.

Main metrics: `zpa_device_lqi`, `zpa_device_lqi_median`, `zpa_device_rssi_dbm`, `zpa_device_available`,
`zpa_device_outages`, `zpa_device_parent_changes`, `zpa_device_weak`, `zpa_link_lqi_median`,
`zpa_recommendation_devices{room,confidence}`, `zpa_recommendation_status{status}`, `zpa_channel_overlap`,
`zpa_collector_last_success_timestamp_seconds`, `zpa_collector_failures_total{kind}`, `zpa_open_incidents`.

Alert independently of Home Assistant with e.g.
`time() - zpa_collector_last_success_timestamp_seconds > 7200`.

## Data and retention

`data/zpa.sqlite3` (WAL mode). Every night at 03:xx the previous days are aggregated into daily medians, raw
data older than `retention.raw_days` (180) and daily values older than `retention.daily_days` (730) are deleted,
and the file is vacuumed incrementally. Weekly reports: `data/reports/<YYYY-Www>.html`, the newest
`reports_keep` (104) are kept.

## Tests

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

- `tests/test_recommend.py`: synthetic networks with a known correct answer (hallway bridge, two branches,
  router extending reach, tie → low confidence, healthy network, too few days/devices/measurements,
  device next to an anchor, device without room, outage- and parent-change-based weakness, link evidence).
- `tests/test_beforeafter.py`: simulated before/after data (worked, no effect, partial, worse, insufficient).
- `tests/test_e2e.py`: the full service against a fake Home Assistant (WebSocket + REST) and fake
  fritz_exporter: collection, history backfill, web UI, metrics, reports, self-monitoring, channel warning,
  new-router detection with evaluation, retention, read-only guarantee.
- `tests/test_units.py`: channel math, config validation, ZHA parsing and schema checks, storage.

## Adding Zigbee2MQTT

Implement `zpa.sources.base.Source.collect()` in `zpa/sources/z2m.py` (e.g. from the
`bridge/request/networkmap` response and availability topics), return the source-independent model from
`zpa/model.py`, and register it in `zpa/sources/__init__.py`. Storage, analysis, recommendation and UI stay
unchanged.

## Limitations

- Rooms and their adjacency are a coarse model; walls, floors and furniture are not modelled. Big rooms or
  devices at the far end of a room can behave differently.
- ZHA does not report RSSI per neighbor link, only per device (last hop to the coordinator).
- The Wi-Fi channel width (20/40 MHz) is unknown to the tool; the check assumes 20 MHz.

## License

MIT, see [LICENSE](LICENSE).
