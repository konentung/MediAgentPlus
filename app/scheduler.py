from __future__ import annotations

import atexit
import os
from collections.abc import Callable
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask

from app.core.exceptions import ExternalServiceError


def init_scheduler(app: Flask) -> BackgroundScheduler | None:
    if not app.config.get("SCHEDULER_ENABLED", True):
        return None
    # `flask run --debug` 的 reloader 會開兩個程序，只在真正跑 app 的子程序啟動，避免跑兩次
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return None

    container = app.extensions["container"]
    scheduler = BackgroundScheduler(
        timezone="UTC",
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 120},
    )
    scheduler.add_job(
        _wrap(app, "reminder.run_due", lambda: container.reminders.run_due()),
        "interval",
        minutes=5,
        id="reminder_run_due",
    )
    scheduler.add_job(
        _wrap(app, "triage.poll", lambda: container.triage.poll()),
        "interval",
        minutes=15,
        id="triage_poll",
        next_run_time=datetime.now(UTC),
    )
    scheduler.start()
    atexit.register(lambda: scheduler.running and scheduler.shutdown(wait=False))
    app.extensions["scheduler"] = scheduler
    app.logger.info("scheduler started: reminder.run_due every 5 min, triage.poll every 15 min")
    return scheduler


def _wrap(app: Flask, name: str, fn: Callable[[], dict]) -> Callable[[], None]:
    """在 app context 內執行、記 log；任何例外只記錄不讓排程器停掉。單 worker 部署，不需要鎖。"""

    def run() -> None:
        with app.app_context():
            try:
                app.logger.info("%s: %s", name, fn())
            except ExternalServiceError as exc:
                app.logger.warning("%s failed: %s", name, exc.message)
            except Exception:
                app.logger.exception("%s crashed", name)

    return run
