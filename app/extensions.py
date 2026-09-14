"""Flask 擴充套件：集中初始化，避免循環匯入。"""

from __future__ import annotations

from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
migrate = Migrate()
