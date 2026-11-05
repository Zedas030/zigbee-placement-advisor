"""Command line: python -m zpa {run,check,report,maintenance} [--config PATH]"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
import time

from aiohttp import web

from . import __version__
from .config import ConfigError, load


def _setup_logging(level: str) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


async def _run(cfg: dict, token: str) -> None:
    from .metrics import attach
    from .service import App
    from .web import create_web_app

    app = App(cfg, token)
    app.started_ts = time.time()
    attach(app)
    app.metrics.update(app)
    runner = web.AppRunner(create_web_app(app), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, cfg["web"]["host"], cfg["web"]["port"])
    await site.start()
    logging.info("zigbee-placement-advisor %s listening on %s:%s", __version__, cfg["web"]["host"], cfg["web"]["port"])
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, app.stop)
        except NotImplementedError:  # Windows
            pass
    try:
        await app.run()
    finally:
        await runner.cleanup()
        app.db.close()


async def _check(cfg: dict, token: str) -> int:
    """Read-only connectivity check: one collection into a temporary database."""
    import tempfile

    from .db import DB
    from .service import App

    with tempfile.TemporaryDirectory() as tmp:
        cfg = dict(cfg, data_dir=tmp)
        cfg["notify"] = dict(cfg["notify"], services=[])
        app = App(cfg, token, db=DB(f"{tmp}/check.sqlite3"))
        summary = await app.collect_once()
        out = {k: v for k, v in summary.items() if k != "new_devices"}
        out["open_incidents"] = [dict(r) for r in app.monitor.open_incidents()]
        out["devices_detail"] = [
            {"name": r["name"], "type": r["device_type"], "room": r["area"], "power": r["power_source"]}
            for r in app.db.devices()
        ]
        out["outages_backfilled"] = len(app.db.outages(0))
        out["channel"] = app.channel_text(hist=app.channel_history())
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
        app.db.close()
        return 0 if summary.get("ok") else 1


async def _report(cfg: dict, token: str, notify: bool) -> int:
    from .service import App
    from .report import write_report

    app = App(cfg, token)
    path = await write_report(app, app.now(), notify=notify)
    print(path)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="zpa", description="Zigbee placement advisor")
    p.add_argument("command", choices=["run", "check", "report", "maintenance", "notify-test", "healthcheck", "version"])
    p.add_argument("--config", default="/config/config.yaml")
    p.add_argument("--notify", action="store_true", help="report: also send the notification")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args(argv)
    _setup_logging(args.log_level)
    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "healthcheck":  # used by the Docker HEALTHCHECK
        import urllib.request

        try:
            cfg, _ = load(args.config)
            with urllib.request.urlopen(f"http://127.0.0.1:{cfg['web']['port']}/healthz", timeout=8) as r:
                return 0 if r.status == 200 else 1
        except Exception:  # noqa: BLE001
            return 1
    try:
        cfg, token = load(args.config)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    if args.command == "run":
        asyncio.run(_run(cfg, token))
        return 0
    if args.command == "check":
        return asyncio.run(_check(cfg, token))
    if args.command == "report":
        return asyncio.run(_report(cfg, token, args.notify))
    if args.command == "notify-test":
        from .i18n import Translator
        from .notify import Notifier

        t = Translator(cfg["language"])
        ok = asyncio.run(Notifier(cfg, token).send(t("app_title"), t("notify_test"), cfg["web"]["public_url"]))
        print("delivered" if ok else "NOT delivered (see log)")
        return 0 if ok else 1
    if args.command == "maintenance":
        from .service import App

        print(json.dumps(App(cfg, token).maintenance(time.time()), indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
