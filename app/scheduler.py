"""程序內排程（APScheduler），取代外部 cron。

- 每 5 分鐘：`ReminderService.run_due()` 推播到期的服藥 / 慢箋提醒
- 每 15 分鐘：`TriageService.poll()` 抓健保署急診即時資料存起來（啟動時先抓一次）

多 worker（gunicorn）時每個 worker 都會啟動排程；設定 `REDIS_URL` 後以 Redis `SET NX` 鎖
確保同一輪只有一個 worker 執行，沒有 Redis 就把 worker 數設為 1。
`SCHEDULER_ENABLED=false` 可整個關掉（測試環境預設關）。
"""

from __future__ import annotations

import atexit
import os
from collections.abc import Callable
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask

from app.core.exceptions import ExternalServiceError

Lock = Callable[[str, int], bool]


def init_scheduler(app: Flask) -> BackgroundScheduler | None:
    if not app.config.get("SCHEDULER_ENABLED", True):
        return None
    # `flask run --debug` 的 reloader 會開兩個程序，只在真正跑 app 的子程序啟動，避免跑兩次
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return None

    container = app.extensions["container"]
    lock = _build_lock(app.config.get("REDIS_URL"))
    scheduler = BackgroundScheduler(
        timezone="UTC",
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 120},
    )
    scheduler.add_job(
        _wrap(app, lock, "reminder.run_due", lambda: container.reminders.run_due(), ttl=240),
        "interval",
        minutes=5,
        id="reminder_run_due",
    )
    scheduler.add_job(
        _wrap(app, lock, "triage.poll", lambda: container.triage.poll(), ttl=840),
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


def _wrap(
    app: Flask, lock: Lock, name: str, fn: Callable[[], dict], ttl: int
) -> Callable[[], None]:
    """在 app context 內執行、取鎖、記 log；任何例外只記錄不讓排程器停掉。"""

    def run() -> None:
        with app.app_context():
            if not lock(name, ttl):
                return
            try:
                app.logger.info("%s: %s", name, fn())
            except ExternalServiceError as exc:
                app.logger.warning("%s failed: %s", name, exc.message)
            except Exception:  # noqa: BLE001
                app.logger.exception("%s crashed", name)

    return run


def _build_lock(redis_url: str | None) -> Lock:
    if not redis_url:
        return lambda _name, _ttl: True
    import redis

    client = redis.Redis.from_url(redis_url)
    # 鎖的 TTL 略短於執行間隔：這一輪誰先搶到誰做，其他 worker 直接略過
    return lambda name, ttl: bool(client.set(f"scheduler:{name}", "1", nx=True, ex=ttl))
