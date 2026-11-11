"""WSGI 進入點：gunicorn / flask CLI 皆由此載入。"""

from app import create_app

app = create_app()
