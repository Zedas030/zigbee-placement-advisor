"""User-facing texts in English and German. Select with `language:` in config.yaml."""
from __future__ import annotations

from typing import Any

TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "app_title": "Zigbee Placement Advisor",
        "nav_overview": "Overview", "nav_devices": "Devices", "nav_rooms": "Room setup", "nav_reports": "Reports",
        "observe_only": "Observes and recommends only. It never changes ZHA or the Zigbee channel.",
        # recommendation
        "rec_title": "Recommendation",
        "rec_ok": "A router device in {room} would probably stabilise {count} weak device(s) (confidence: {level}).",
        "rec_alt": "Alternative: {room}, {count} device(s) (confidence: {level}).",
        "rec_healthy": "No weak devices – no additional router needed right now.",
        "rec_insufficient": "Not enough data yet – no recommendation.",
        "rec_setup": "Room setup missing: confirm rooms, neighbouring rooms and the coordinator's room first.",
        "rec_no_candidate": "Weak devices found, but no room clearly helps.",
        "reason_setup": "Room setup has not been confirmed yet.",
        "reason_few_devices": "Only {have} Zigbee devices (need at least {need}).",
        "reason_few_days": "Only {have} days of data (need at least {need}).",
        "reason_few_measurements": "Only {have} measurements (need at least {need}).",
        "reason_no_anchor": "No good router or coordinator is located in a configured room.",
        "reason_no_candidate": "No room lies within reach of both a weak device and a good router.",
        "basis": "Data basis: {days} days, {measurements} measurements, {scans} neighbor-table scans, {devices} devices.",
        "estimate_note": "This is an estimate from room adjacency and measured link quality, not a radio simulation.",
        "level_high": "high", "level_medium": "medium", "level_low": "low",
        "unplaced": "Weak devices without a room in Home Assistant (assign an area): {names}",
        "not_distance": "Weak although a good router is in the same room – distance is probably not the cause (interference, metal, the device itself): {names}",
        "unreachable": "Weak devices no single new router can reach: {names}",
        "stabilises": "stabilises", "confidence": "Confidence",
        # devices
        "col_device": "Device", "col_room": "Room", "col_type": "Type", "col_power": "Power",
        "col_lqi_median": "LQI median", "col_lqi_last": "LQI last", "col_outages": "Outages",
        "col_parent_changes": "Parent changes", "col_samples": "Samples", "col_state": "State",
        "col_parent": "Parent", "col_trend": "Trend", "col_neighbor": "Neighbor", "col_relationship": "Relationship",
        "weak": "weak", "good": "good", "ok": "ok", "anchor": "good router", "no_room": "no room",
        "available": "available", "unavailable": "unavailable",
        "reason_lqi": "low LQI", "reason_outages": "frequent outages", "reason_parents": "frequent parent changes",
        "type_Coordinator": "Coordinator", "type_Router": "Router", "type_EndDevice": "End device",
        "window": "Analysis window: last {days} days.",
        "lqi_over_time": "LQI over time", "outage_list": "Outages", "links": "Measured neighbor links",
        "no_outages": "No outages.", "system_event": "HA/ZHA restart (all devices, not counted)",
        "legend_outage": "device outage", "legend_weak": "weak threshold", "range": "Range", "days_n": "{n} days",
        "system_events": "System-wide events (HA/ZHA restart, not counted against devices): {n}",
        # channel
        "channel_title": "Channel check",
        "channel_ok": "Zigbee channel {zigbee} and Wi-Fi channel {wifi} do not overlap ({mhz} MHz apart, the Zigbee channel sits in a gap of the Wi-Fi signal). Assumes 20 MHz Wi-Fi width.",
        "channel_overlap": "Warning: Zigbee channel {zigbee} overlaps Wi-Fi channel {wifi} ({mhz} MHz apart). Consider changing the Wi-Fi channel. This tool changes nothing.",
        "channel_unknown": "Channel check not possible (Zigbee {zigbee}, Wi-Fi {wifi}).",
        # health
        "health_title": "Data collection",
        "health_ok": "Last successful collection {ago} ago ({source}).",
        "health_never": "No successful collection yet.",
        "health_incidents": "Open problems",
        "next_scan_note": "ZHA refreshes neighbor tables itself every 4 h; the tool only reads that cache (no extra radio traffic).",
        # rooms setup
        "setup_title": "Room setup",
        "setup_help": "1. Untick rooms without Zigbee coverage you care about. 2. Choose the coordinator's room. 3. Click a room, then click every room that borders it (shared wall, door or open passage). Click again to remove.",
        "setup_enabled": "Use room", "setup_coordinator": "Coordinator is here", "setup_neighbors": "Neighbouring rooms",
        "setup_save": "Save", "setup_saved": "Saved.", "setup_confirmed": "Last confirmed: {when}",
        "setup_pairs": "Neighbour pairs (also editable without JavaScript)", "setup_select_hint": "Selected: {room} – now click its neighbours.",
        # reports
        "reports_title": "Weekly reports", "report_now": "Create report now (without notification)",
        "report_week": "Week {week}", "report_none": "No reports yet.",
        "report_generated": "Generated {when}",
        "ba_title": "Before/after", "ba_none": "No new router detected yet.",
        "ba_pending": "New router {name} in {room} detected on {when}; evaluation after {days} days.",
        "verdict_worked": "The new router worked: {improved} of {considered} weak devices improved.",
        "verdict_partial": "Partly worked: {improved} of {considered} weak devices improved, {worse} got worse.",
        "verdict_no_effect": "No measurable effect on the {considered} weak devices.",
        "verdict_worse": "Got worse: {worse} of {considered} weak devices deteriorated.",
        "verdict_insufficient_data": "Not enough data before/after to judge.",
        "verdict_nothing_weak": "There were no weak devices before the router was added, so there is nothing to compare.",
        "as_recommended_yes": "Placed in the recommended room.", "as_recommended_no": "Placed in {room}; recommended was {rec}.",
        "before": "before", "after": "after",
        # notifications
        "notify_weekly_title": "Zigbee weekly report",
        "notify_incident_title": "Zigbee advisor: problem",
        "notify_resolved_title": "Zigbee advisor: resolved",
        "notify_ba_title": "Zigbee: new router evaluated",
        "notify_channel_title": "Zigbee: channel overlap",
        "summary_weak": "{weak} of {total} devices weak.",
        "summary_outages": "{n} device outages this week.",
        "inc_connection": "Home Assistant has not been reachable for {cycles} collection cycles: {detail}",
        "inc_auth": "Home Assistant rejects the token (HA_TOKEN in .env). Create a new long-lived token. {detail}",
        "inc_schema": "The ZHA API answer changed (probably after an update); no data is being collected: {detail}",
        "inc_no_data": "ZHA delivers no data: {detail}",
        "inc_stale": "No Zigbee device has reported anything for {minutes} minutes – ZHA or the coordinator may be stuck.",
        "inc_wifi": "The Wi-Fi channel cannot be read: {detail}",
        "inc_notify": "Notify service notify.{service} does not exist in Home Assistant.",
        "inc_resolved": "Resolved: {message}",
        "inc_internal": "Internal error in the collection cycle, see the container log (docker logs zigbee-placement-advisor).",
        "ago_min": "{n} min", "ago_h": "{n} h", "ago_d": "{n} d",
        "never": "never", "unknown": "unknown",
        "notify_test": "Test notification: notifications from the Zigbee placement advisor arrive.",
    },
    "de": {
        "app_title": "Zigbee-Platzberater",
        "nav_overview": "Übersicht", "nav_devices": "Geräte", "nav_rooms": "Raummodell", "nav_reports": "Berichte",
        "observe_only": "Beobachtet und empfiehlt nur. Ändert nie etwas an ZHA oder am Zigbee-Kanal.",
        "rec_title": "Empfehlung",
        "rec_ok": "Ein Router-Gerät im Raum {room} würde voraussichtlich {count} schwache(s) Gerät(e) stabilisieren (Sicherheit: {level}).",
        "rec_alt": "Alternative: {room}, {count} Gerät(e) (Sicherheit: {level}).",
        "rec_healthy": "Keine schwachen Geräte – zurzeit ist kein zusätzlicher Router nötig.",
        "rec_insufficient": "Noch zu wenig Daten – keine Empfehlung.",
        "rec_setup": "Raummodell fehlt: Bitte zuerst Räume, Nachbarräume und den Raum des Coordinators bestätigen.",
        "rec_no_candidate": "Schwache Geräte gefunden, aber kein Raum hilft eindeutig.",
        "reason_setup": "Das Raummodell wurde noch nicht bestätigt.",
        "reason_few_devices": "Nur {have} Zigbee-Geräte (mindestens {need} nötig).",
        "reason_few_days": "Erst {have} Tage Daten (mindestens {need} nötig).",
        "reason_few_measurements": "Erst {have} Messungen (mindestens {need} nötig).",
        "reason_no_anchor": "Kein guter Router und kein Coordinator steht in einem eingerichteten Raum.",
        "reason_no_candidate": "Kein Raum liegt in Reichweite eines schwachen Geräts und zugleich eines guten Routers.",
        "basis": "Datengrundlage: {days} Tage, {measurements} Messungen, {scans} Nachbartabellen-Scans, {devices} Geräte.",
        "estimate_note": "Das ist eine Schätzung aus Raumnachbarschaft und gemessener Linkqualität, keine Funksimulation.",
        "level_high": "hoch", "level_medium": "mittel", "level_low": "niedrig",
        "unplaced": "Schwache Geräte ohne Raum in Home Assistant (bitte Bereich zuweisen): {names}",
        "not_distance": "Schwach, obwohl ein guter Router im selben Raum steht – die Entfernung ist vermutlich nicht die Ursache (Störquelle, Metall, Gerät selbst): {names}",
        "unreachable": "Schwache Geräte, die kein einzelner neuer Router erreicht: {names}",
        "stabilises": "stabilisiert", "confidence": "Sicherheit",
        "col_device": "Gerät", "col_room": "Raum", "col_type": "Typ", "col_power": "Strom",
        "col_lqi_median": "LQI-Median", "col_lqi_last": "LQI zuletzt", "col_outages": "Ausfälle",
        "col_parent_changes": "Parent-Wechsel", "col_samples": "Messungen", "col_state": "Zustand",
        "col_parent": "Parent", "col_trend": "Verlauf", "col_neighbor": "Nachbar", "col_relationship": "Beziehung",
        "weak": "schwach", "good": "gut", "ok": "ok", "anchor": "guter Router", "no_room": "kein Raum",
        "available": "verfügbar", "unavailable": "nicht verfügbar",
        "reason_lqi": "niedriger LQI", "reason_outages": "häufige Ausfälle", "reason_parents": "häufige Parent-Wechsel",
        "type_Coordinator": "Coordinator", "type_Router": "Router", "type_EndDevice": "Endgerät",
        "window": "Auswertungszeitraum: letzte {days} Tage.",
        "lqi_over_time": "LQI im Zeitverlauf", "outage_list": "Ausfälle", "links": "Gemessene Nachbar-Links",
        "no_outages": "Keine Ausfälle.", "system_event": "HA-/ZHA-Neustart (alle Geräte, zählt nicht)",
        "legend_outage": "Geräteausfall", "legend_weak": "Schwelle „schwach“", "range": "Zeitraum", "days_n": "{n} Tage",
        "system_events": "Netzweite Ereignisse (HA-/ZHA-Neustart, zählen nicht gegen Geräte): {n}",
        "channel_title": "Kanal-Check",
        "channel_ok": "Zigbee-Kanal {zigbee} und WLAN-Kanal {wifi} überlappen nicht ({mhz} MHz Abstand, der Zigbee-Kanal liegt in einer Lücke des WLAN-Signals). Annahme: 20 MHz WLAN-Breite.",
        "channel_overlap": "Warnung: Zigbee-Kanal {zigbee} überlappt mit WLAN-Kanal {wifi} ({mhz} MHz Abstand). Ein anderer WLAN-Kanal wäre besser. Das Tool ändert nichts.",
        "channel_unknown": "Kanal-Check nicht möglich (Zigbee {zigbee}, WLAN {wifi}).",
        "health_title": "Datensammlung",
        "health_ok": "Letzte erfolgreiche Abfrage vor {ago} ({source}).",
        "health_never": "Noch keine erfolgreiche Abfrage.",
        "health_incidents": "Offene Probleme",
        "next_scan_note": "ZHA aktualisiert die Nachbartabellen selbst alle 4 h; das Tool liest nur diesen Zwischenspeicher (kein zusätzlicher Funkverkehr).",
        "setup_title": "Raummodell",
        "setup_help": "1. Häkchen bei Räumen entfernen, die egal sind. 2. Raum des Coordinators wählen. 3. Einen Raum anklicken, dann alle Räume anklicken, die daran grenzen (gemeinsame Wand, Tür oder offener Durchgang). Nochmal klicken entfernt.",
        "setup_enabled": "Raum verwenden", "setup_coordinator": "Coordinator steht hier", "setup_neighbors": "Nachbarräume",
        "setup_save": "Speichern", "setup_saved": "Gespeichert.", "setup_confirmed": "Zuletzt bestätigt: {when}",
        "setup_pairs": "Nachbarpaare (auch ohne JavaScript bearbeitbar)", "setup_select_hint": "Ausgewählt: {room} – jetzt die Nachbarräume anklicken.",
        "reports_title": "Wochenberichte", "report_now": "Bericht jetzt erstellen (ohne Benachrichtigung)",
        "report_week": "Woche {week}", "report_none": "Noch keine Berichte.",
        "report_generated": "Erstellt {when}",
        "ba_title": "Vorher/Nachher", "ba_none": "Noch kein neuer Router erkannt.",
        "ba_pending": "Neuer Router {name} im Raum {room} erkannt am {when}; Auswertung nach {days} Tagen.",
        "verdict_worked": "Der neue Router hat gewirkt: {improved} von {considered} schwachen Geräten sind besser geworden.",
        "verdict_partial": "Teilweise gewirkt: {improved} von {considered} schwachen Geräten besser, {worse} schlechter.",
        "verdict_no_effect": "Keine messbare Wirkung auf die {considered} schwachen Geräte.",
        "verdict_worse": "Schlechter geworden: {worse} von {considered} schwachen Geräten haben sich verschlechtert.",
        "verdict_insufficient_data": "Zu wenig Daten vorher/nachher für eine Bewertung.",
        "verdict_nothing_weak": "Vor dem neuen Router gab es keine schwachen Geräte, also nichts zu vergleichen.",
        "as_recommended_yes": "Im empfohlenen Raum aufgestellt.", "as_recommended_no": "Aufgestellt im Raum {room}; empfohlen war {rec}.",
        "before": "vorher", "after": "nachher",
        "notify_weekly_title": "Zigbee-Wochenbericht",
        "notify_incident_title": "Zigbee-Platzberater: Problem",
        "notify_resolved_title": "Zigbee-Platzberater: behoben",
        "notify_ba_title": "Zigbee: neuer Router ausgewertet",
        "notify_channel_title": "Zigbee: Kanal-Überlappung",
        "summary_weak": "{weak} von {total} Geräten schwach.",
        "summary_outages": "{n} Geräteausfälle diese Woche.",
        "inc_connection": "Home Assistant ist seit {cycles} Abfragen nicht erreichbar: {detail}",
        "inc_auth": "Home Assistant lehnt das Token ab (HA_TOKEN in .env). Bitte ein neues langlebiges Token anlegen. {detail}",
        "inc_schema": "Die Antwort der ZHA-API hat sich geändert (vermutlich nach einem Update); es werden keine Daten gesammelt: {detail}",
        "inc_no_data": "ZHA liefert keine Daten: {detail}",
        "inc_stale": "Seit {minutes} Minuten hat sich kein Zigbee-Gerät gemeldet – ZHA oder der Coordinator hängt vielleicht.",
        "inc_wifi": "Der WLAN-Kanal lässt sich nicht lesen: {detail}",
        "inc_notify": "Den notify-Dienst notify.{service} gibt es in Home Assistant nicht.",
        "inc_resolved": "Behoben: {message}",
        "inc_internal": "Interner Fehler im Sammelzyklus, siehe Container-Log (docker logs zigbee-placement-advisor).",
        "ago_min": "{n} Min.", "ago_h": "{n} Std.", "ago_d": "{n} Tagen",
        "never": "nie", "unknown": "unbekannt",
        "notify_test": "Testmeldung: Benachrichtigungen vom Zigbee-Platzberater kommen an.",
    },
}


class Translator:
    def __init__(self, language: str):
        self.language = language if language in TEXTS else "en"

    def __call__(self, key: str, **params: Any) -> str:
        text = TEXTS[self.language].get(key) or TEXTS["en"].get(key) or key
        params = {k: self.number(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
                  for k, v in params.items()}
        try:
            return text.format(**params)
        except (KeyError, IndexError):
            return text

    def number(self, value: float) -> str:
        if isinstance(value, int) or float(value).is_integer() and abs(value) >= 1000:
            text = f"{int(value):,}"
            return text.replace(",", ".") if self.language == "de" else text
        text = f"{value:.1f}" if isinstance(value, float) else str(value)
        return text.replace(".", ",") if self.language == "de" else text

    def ago(self, seconds: float | None) -> str:
        if seconds is None:
            return self("never")
        if seconds < 5400:
            return self("ago_min", n=max(0, int(seconds // 60)))
        if seconds < 172800:
            return self("ago_h", n=int(seconds // 3600))
        return self("ago_d", n=int(seconds // 86400))


def check_complete() -> list[str]:
    """Keys missing in one of the languages (used by the test suite)."""
    en, de = set(TEXTS["en"]), set(TEXTS["de"])
    return sorted((en - de) | (de - en))
