"""排程啟動判斷：每一種啟動方式都要剛好有一個程序跑排程，否則服藥提醒推不出去或重複推。"""

from __future__ import annotations

import pytest

from app.scheduler import should_start

CLI = {"FLASK_RUN_FROM_CLI": "true"}
RUN = ["--app", "wsgi", "run"]


@pytest.mark.parametrize(
    ("argv", "environ", "expected"),
    [
        # gunicorn：不是 flask CLI，開發環境（DEBUG=True）也要啟動
        ([], {}, True),
        # flask run 沒加 --debug：沒有 reloader，這個程序就要啟動
        (RUN, CLI, True),
        # flask run --debug：父程序不啟動、reloader 子程序啟動
        ([*RUN, "--debug"], {**CLI, "FLASK_DEBUG": "1"}, False),
        ([*RUN, "--debug"], {**CLI, "FLASK_DEBUG": "1", "WERKZEUG_RUN_MAIN": "true"}, True),
        # 只設 FLASK_DEBUG=1 也會開 reloader
        (RUN, {**CLI, "FLASK_DEBUG": "1"}, False),
        # IDE 除錯常用的 --no-reload：只有一個程序
        ([*RUN, "--debug", "--no-reload"], {**CLI, "FLASK_DEBUG": "1"}, True),
        ([*RUN, "--no-debug"], {**CLI, "FLASK_DEBUG": "1"}, True),
        # 明確要求 reloader
        ([*RUN, "--reload"], CLI, False),
        # 其他 flask 指令不跑排程
        (["--app", "wsgi", "db", "upgrade"], CLI, False),
        (["--app", "wsgi", "routes"], CLI, False),
    ],
)
def test_should_start(argv: list[str], environ: dict[str, str], expected: bool) -> None:
    assert should_start(argv, environ) is expected
