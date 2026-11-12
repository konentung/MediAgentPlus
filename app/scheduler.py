from __future__ import annotations

import atexit
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask

from app.core.exceptions import ExternalServiceError


def init_scheduler(app: Flask) -> BackgroundScheduler | None:
    if not app.config.get("SCHEDULER_ENABLED", True):
        return None
    if not should_start(sys.argv[1:], os.environ):
        app.logger.info("scheduler not started in this process (reloader parent or non-run CLI)")
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


def should_start(argv: Sequence[str], environ: Mapping[str, str]) -> bool:
    """這個程序該不該啟動排程：每一種啟動方式都只能有一個程序在跑。

    不能用 `app.debug` 判斷有沒有 reloader：`DevelopmentConfig.DEBUG = True` 讓它永遠是 True，
    `flask run`（沒加 `--debug`）、`--no-reload`、開發環境的 gunicorn 都沒有 reloader 子程序，
    排程就完全不會啟動、提醒永遠推不出去。
    """
    # reloader 的子程序：真正跑 app 的那一個
    if environ.get("WERKZEUG_RUN_MAIN") == "true":
        return True
    # 不是 flask CLI（gunicorn 等）：只有這一個程序
    if environ.get("FLASK_RUN_FROM_CLI") != "true":
        return True
    # flask CLI：只有 `flask run` 要跑排程（`flask db upgrade`、`flask routes` 不用）
    if "run" not in argv:
        return False
    # 與 flask run 相同的規則：reloader 預設跟著 debug 模式（FLASK_DEBUG / --debug）開
    debug = "--debug" in argv or (
        "--no-debug" not in argv
        and environ.get("FLASK_DEBUG", "").lower() not in {"", "0", "false", "no"}
    )
    uses_reloader = "--reload" in argv or (debug and "--no-reload" not in argv)
    # 有 reloader 時這裡是負責監看檔案的父程序，排程交給子程序
    return not uses_reloader


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
