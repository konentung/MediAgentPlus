"""錯誤處理：API 回 JSON，瀏覽器請求 render 錯誤頁。"""

from app.core.exceptions import AuthorizationError


def test_api_404_returns_json(client):
    resp = client.get("/api/v1/does-not-exist")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "not_found"


def test_browser_404_renders_html(client):
    resp = client.get("/does-not-exist", headers={"Accept": "text/html"})
    assert resp.status_code == 404
    assert resp.mimetype == "text/html"
    html = resp.get_data(as_text=True)
    assert "找不到頁面" in html and 'href="/"' in html


def test_browser_path_with_json_accept_returns_json(client):
    resp = client.get("/does-not-exist", headers={"Accept": "application/json"})
    assert resp.status_code == 404
    assert resp.get_json()["success"] is False


def test_app_error_and_unhandled_exception_render_html(app, client):
    @app.get("/boom")
    def boom():
        raise RuntimeError("kaboom")

    @app.get("/forbidden")
    def forbidden():
        raise AuthorizationError("你沒有權限")

    # 測試模式預設會把未處理例外往外丟；這裡要驗證 500 錯誤頁，所以關掉
    app.config["PROPAGATE_EXCEPTIONS"] = False

    resp = client.get("/boom", headers={"Accept": "text/html"})
    assert resp.status_code == 500
    html = resp.get_data(as_text=True)
    assert "伺服器發生錯誤" in html and "kaboom" not in html

    resp = client.get("/forbidden", headers={"Accept": "text/html"})
    assert resp.status_code == 403
    assert "你沒有權限" in resp.get_data(as_text=True)


def test_upload_over_size_limit_returns_clear_message(app, client, make_user, auth_header):
    """超過 MAX_CONTENT_LENGTH → 413，訊息要講清楚上限與做法（不是 werkzeug 的英文說明）。"""
    import io

    from app.domain.enums import Role

    admin = make_user(Role.SUPER_ADMIN)
    app.extensions["container"].knowledge_jobs.spawn = lambda fn: fn()
    megabyte = 1024 * 1024

    def upload(size):
        return client.post(
            "/api/v1/admin/knowledge/jobs/pdf-import",
            data={"files": [(io.BytesIO(b"x" * size), "big.pdf")]},
            headers=auth_header(admin),
        )

    original = {k: app.config[k] for k in ("MAX_CONTENT_LENGTH", "KNOWLEDGE_UPLOAD_MAX_BYTES")}
    app.config.update(MAX_CONTENT_LENGTH=megabyte, KNOWLEDGE_UPLOAD_MAX_BYTES=4 * megabyte)
    try:
        # 衛教 PDF 端點用自己較寬的上限：超過全站上限但沒超過 PDF 上限 → 收下（背景工作照常建立）
        assert upload(3 * megabyte).status_code == 202
        over = upload(5 * megabyte)
        # 其他端點仍是全站上限
        other = client.post(
            "/api/v1/admin/hospitals/import",
            data={"file": (io.BytesIO(b"x" * (2 * megabyte)), "h.csv")},
            headers=auth_header(admin),
        )
    finally:
        app.config.update(original)
    assert over.status_code == 413
    message = over.get_json()["error"]["message"]
    assert "不可超過 4 MB" in message and "分批上傳" in message
    assert other.status_code == 413 and "不可超過 1 MB" in other.get_json()["error"]["message"]
