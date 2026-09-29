"""2026-09-29 全面檢查時修正的問題，各留一個回歸測試。"""

from __future__ import annotations

import io

from app.domain.enums import Intent, Role
from app.line.intents import postback_data
from tests.test_assistant import _HYPERTENSION, _record
from tests.test_reminder import _create_prescription
from tests.test_webhook import _event

_SEP = "=" * 60


def _actor(container, make_user, role=Role.SUPER_ADMIN, hospital_id=None):
    return container.users.to_principal(make_user(role, hospital_id=hospital_id))


# ---- API：JSON body 不是物件 / 欄位不是字串 → 422，不是 500 ----
def test_json_array_body_returns_422(client, container, make_user, auth_header):
    assert client.post("/api/v1/liff/auth", json=[1]).status_code == 422
    admin = make_user(Role.SUPER_ADMIN)
    resp = client.post("/api/v1/admin/knowledge/jobs/embed", json=[1], headers=auth_header(admin))
    assert resp.status_code == 422
    resp = client.post(
        "/api/v1/admin/hospitals/import", json={"rows": [1]}, headers=auth_header(admin)
    )
    assert resp.status_code == 422


def test_non_string_fields_are_normalized(client, container, make_user, auth_header):
    staff = make_user(Role.HOSPITAL_STAFF, hospital_id="H1")
    fb = container.feedback.submit(
        container.users.to_principal(make_user(Role.GUEST, hospital_id="H1")),
        {"category": "other", "content": "測試回饋內容"},
    )
    resp = client.patch(
        f"/api/v1/admin/feedback/{fb['id']}",
        json={"status": "resolved", "note": 123},
        headers=auth_header(staff),
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"]["resolver_note"] == "123"


# ---- intake：NaN 不能繞過範圍檢查 ----
def test_intake_rejects_nan(client, make_user, auth_header):
    user = make_user(Role.GUEST)
    resp = client.put(
        "/api/v1/liff/intake/steps/3",
        json={"height_cm": "nan", "weight_kg": 60, "partial": True},
        headers=auth_header(user),
    )
    assert resp.status_code == 422


# ---- reminders：prescription_id 必須是自己的處方；PATCH 不能把 times 清空 ----
def test_reminder_rejects_foreign_prescription(client, container, make_user, auth_header):
    user = make_user(Role.GUEST)
    draft = {
        "drug_name": "A",
        "frequency_code": "qd",
        "times": ["08:00"],
        "prescription_id": "nope",
    }
    resp = client.post(
        "/api/v1/liff/reminders", json={"schedules": [draft]}, headers=auth_header(user)
    )
    assert resp.status_code == 422


def test_reminder_patch_cannot_clear_times(client, make_user, auth_header):
    user = make_user(Role.GUEST)
    headers = auth_header(user)
    draft = {"drug_name": "A", "frequency_code": "qd", "times": ["08:00"]}
    created = client.post(
        "/api/v1/liff/reminders", json={"schedules": [draft]}, headers=headers
    ).get_json()["data"]
    sid = created[0]["id"]
    resp = client.patch(f"/api/v1/liff/reminders/{sid}", json={"times": []}, headers=headers)
    assert resp.status_code == 422


# ---- 處方辨識：日期格式寬鬆 ----
def test_ocr_date_lenient(post_line_event, container):
    container.vision_client.result = container.vision_client.result.model_copy(
        update={"issued_on": "113/05/01"}
    )
    pid = _create_prescription(post_line_event, container)
    row = container.prescription_repo.get(pid)
    assert str(row["issued_on"]).startswith("2024-05-01")


# ---- 知識庫：同名檔案在通用 + 院所各一份仍可更新；SUPER_ADMIN 指定不存在的院所 → 422 ----
def test_import_markdown_same_name_generic_and_hospital(client, make_user, auth_header):
    md = "---\ntitle: 同名\ncategory: education\n---\n內文 傷口 照護 說明"
    superadmin, h1 = make_user(Role.SUPER_ADMIN), make_user(Role.HOSPITAL_ADMIN, hospital_id="H1")
    for who in (superadmin, h1, superadmin, h1):
        resp = client.post(
            "/api/v1/admin/knowledge/import",
            data={"files": (io.BytesIO(md.encode()), "same.md")},
            headers=auth_header(who),
        )
        assert resp.status_code == 200, resp.get_json()
        assert not resp.get_json()["data"]["errors"]
    docs = client.get("/api/v1/admin/knowledge", headers=auth_header(superadmin)).get_json()
    same = [d for d in docs["data"] if d["source"] == "same.md"]
    assert {d["hospital_id"] for d in same} == {None, "H1"} and len(same) == 2
    assert "content" not in same[0]  # 列表不帶內文


def test_create_document_unknown_hospital_returns_422(client, make_user, auth_header):
    resp = client.post(
        "/api/v1/admin/knowledge",
        json={"title": "x", "category": "education", "content": "內文" * 10, "hospital_id": "NOPE"},
        headers=auth_header(make_user(Role.SUPER_ADMIN)),
    )
    assert resp.status_code == 422


def test_department_import_same_url_twice_in_one_batch(container, make_user, tmp_path):
    text = _HYPERTENSION
    body = _record("高血壓居家照護", "台大醫院", "醫學中心", "https://x.example/1", text) + _record(
        "高血壓居家照護（更新）",
        "台大醫院",
        "醫學中心",
        "https://x.example/1",
        text + "\n\n新增一段內容。",
    )
    path = tmp_path / "01_心臟內科.txt"
    path.write_text("header\n\n" + body, encoding="utf-8")
    result = container.knowledge.import_department_files(_actor(container, make_user), [path])
    assert result["created"] == 1 and result["updated"] == 0 and result["duplicates"] == 1
    assert container.knowledge.stats()["documents"] == 1


def test_department_header_ignores_url_lines_in_body(container, make_user, tmp_path):
    text = _HYPERTENSION + "\n\n網址：https://x.example/contact\n\n以上內容僅供參考。"
    path = tmp_path / "01_心臟內科.txt"
    path.write_text(
        "h\n\n" + _record("高血壓居家照護", "台大醫院", "醫學中心", "https://x.example/1", text),
        encoding="utf-8",
    )
    result = container.knowledge.import_department_files(_actor(container, make_user), [path])
    assert result["created"] == 1
    doc = container.knowledge.list(_actor(container, make_user))[0]
    assert doc["source"] == "https://x.example/1" and doc["meta"]["hospital_name"] == "台大醫院"


# ---- 院所選單要包含登入者自己的院所（即使是診所） ----
def test_hospital_options_include_own_clinic(client, container, make_user, auth_header):
    container.hospital_repo.create(id="C1", name="小診所", capacity_tier="clinic", active=True)
    admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="C1")
    ids = {
        h["id"]
        for h in client.get(
            "/api/v1/admin/hospitals/options", headers=auth_header(admin)
        ).get_json()["data"]
    }
    assert "C1" in ids
    other = make_user(Role.HOSPITAL_ADMIN, hospital_id="H1")
    ids = {
        h["id"]
        for h in client.get(
            "/api/v1/admin/hospitals/options", headers=auth_header(other)
        ).get_json()["data"]
    }
    assert "C1" not in ids


# ---- 沒有 OPENAI_API_KEY：可以啟動，AI 功能退回關鍵字 ----
def test_app_starts_without_openai_key(post_line_event, container):
    from app.integrations.llm import DisabledLlmClient

    container.llm_client = DisabledLlmClient("m")
    resp = post_line_event(
        _event(
            "message",
            message={"type": "text", "id": "1", "text": "我想找醫院看急診", "quoteToken": "q"},
        )
    )
    assert resp.status_code == 200
    assert container.line_client.replies  # 有回覆（關鍵字退路或失敗訊息）


def test_confirm_postback_unknown_prescription(post_line_event, container):
    ev = _event(
        "postback",
        postback={"data": postback_data(Intent.MEDICATION, action="confirm", prescription_id="x")},
    )
    assert post_line_event(ev).status_code == 200
