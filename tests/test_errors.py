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
