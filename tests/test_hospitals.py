"""院所：CRUD API、權限、以及其他流程對院所存在性的驗證。"""

from __future__ import annotations

import io

from app.domain.enums import Role


def test_public_hospital_list_for_liff(client):
    rows = client.get("/api/v1/liff/hospitals").get_json()["data"]
    assert [r["id"] for r in rows] == ["H1", "H2"]
    assert set(rows[0]) == {"id", "name", "region"}  # 只回公開欄位


def test_only_super_admin_can_create_hospital(client, make_user, auth_header):
    body = {"id": "H3", "name": "第三醫院", "region": "新北市", "capacity_tier": "regional"}
    admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H1")
    assert (
        client.post("/api/v1/admin/hospitals", json=body, headers=auth_header(admin)).status_code
        == 403
    )

    superadmin = make_user(Role.SUPER_ADMIN)
    resp = client.post("/api/v1/admin/hospitals", json=body, headers=auth_header(superadmin))
    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()["data"]["capacity_tier"] == "regional"
    # 代碼重複 / 格式錯誤
    assert (
        client.post(
            "/api/v1/admin/hospitals", json=body, headers=auth_header(superadmin)
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/v1/admin/hospitals",
            json={"id": "bad id!", "name": "x"},
            headers=auth_header(superadmin),
        ).status_code
        == 422
    )


def test_hospital_admin_can_edit_own_hospital_only(client, make_user, auth_header):
    admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H1")
    ok = client.patch(
        "/api/v1/admin/hospitals/H1",
        json={"name": "第一醫院（更名）", "contact_phone": "02-12345678"},
        headers=auth_header(admin),
    )
    assert ok.status_code == 200 and ok.get_json()["data"]["name"] == "第一醫院（更名）"
    assert (
        client.patch(
            "/api/v1/admin/hospitals/H2", json={"name": "x"}, headers=auth_header(admin)
        ).status_code
        == 403
    )
    # 停用只有 SUPER_ADMIN 可以
    assert (
        client.patch(
            "/api/v1/admin/hospitals/H1", json={"active": False}, headers=auth_header(admin)
        ).status_code
        == 403
    )
    superadmin = make_user(Role.SUPER_ADMIN)
    assert (
        client.patch(
            "/api/v1/admin/hospitals/H2", json={"active": False}, headers=auth_header(superadmin)
        ).status_code
        == 200
    )
    # 停用後公開清單看不到，SUPER_ADMIN 帶 all=1 看得到
    assert [h["id"] for h in client.get("/api/v1/liff/hospitals").get_json()["data"]] == ["H1"]
    assert (
        len(
            client.get("/api/v1/admin/hospitals?all=1", headers=auth_header(superadmin)).get_json()[
                "data"
            ]["items"]
        )
        == 2
    )


def test_staff_can_list_hospitals_but_end_user_cannot(client, make_user, auth_header):
    agent = make_user(Role.HOSPITAL_STAFF, hospital_id="H1")
    assert client.get("/api/v1/admin/hospitals", headers=auth_header(agent)).status_code == 200
    patient = make_user(Role.PATIENT)
    assert client.get("/api/v1/admin/hospitals", headers=auth_header(patient)).status_code == 403


def test_unknown_hospital_is_rejected_everywhere(client, make_user, auth_header):
    superadmin = make_user(Role.SUPER_ADMIN)
    staff = make_user(Role.GUEST)
    # 指派角色到不存在的院所
    resp = client.put(
        f"/api/v1/admin/users/{staff.id}/role",
        json={"role": "medical_staff", "hospital_id": "NOPE"},
        headers=auth_header(superadmin),
    )
    assert resp.status_code == 422


NHI_CSV = (
    "\ufeff醫事機構代碼,醫事機構名稱,醫事機構種類,電話,地址,分區業務組\n"
    "0401180014,臺大醫院,醫學中心,(02)23123456,臺北市中正區中山南路7號,臺北業務組\n"
    "3501013600,某某診所,診所,(02)11112222,新北市板橋區xx路1號,臺北業務組\n"
    "5901010076,榮星藥局,藥劑生自營,(02)27124696,臺北市松山區南京東路４段１３３巷５弄１號,臺北業務組\n"
    "bad id,壞代碼,地區醫院,,,\n"
)


def test_import_csv_nhi_format_and_upsert(client, make_user, auth_header):
    superadmin = make_user(Role.SUPER_ADMIN)
    headers = auth_header(superadmin)
    resp = client.post(
        "/api/v1/admin/hospitals/import",
        data={"file": (io.BytesIO(NHI_CSV.encode("utf-8")), "nhi.csv")},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert resp.status_code == 200, resp.get_json()
    result = resp.get_json()["data"]
    assert (result["created"], result["updated"], result["skipped"]) == (2, 0, 1)  # 藥局略過
    assert result["errors"][0]["row"] == 5 and "代碼" in result["errors"][0]["message"]

    rows = {
        h["id"]: h
        for h in client.get("/api/v1/admin/hospitals", headers=headers).get_json()["data"]["items"]
    }
    ntuh = rows["0401180014"]
    assert ntuh["capacity_tier"] == "medical_center" and ntuh["region"] == "臺北市"
    assert rows["3501013600"]["capacity_tier"] == "clinic"

    # 再匯一次：同代碼變更新；only_hospitals 略過診所
    resp = client.post(
        "/api/v1/admin/hospitals/import?only_hospitals=1",
        data={"file": (io.BytesIO(NHI_CSV.encode("utf-8")), "nhi.csv")},
        content_type="multipart/form-data",
        headers=headers,
    )
    result = resp.get_json()["data"]
    assert (result["created"], result["updated"], result["skipped"]) == (0, 1, 2)


MOHW_CSV = (
    "\ufeff機構代碼,機構名稱,電話,縣市區名,地址,科別,A醫師\n"
    '3501015311,禾玥醫學診所,0988863238,臺北市松山區,臺北市松山區南京東路3段269巷6號1樓,"家庭醫學科,內科,",1\n'
    '0401180014,臺大醫院,(02)23123456,臺北市中正區,臺北市中正區中山南路7號,"家庭醫學科,內科,外科,",100\n'
)


def test_import_mohw_format_fills_departments(client, make_user, auth_header):
    """衛福部「醫療機構與人員基本資料」：沒有層級欄位，診所從名稱認、醫院層級不覆蓋；科別寫入 departments。"""
    superadmin = make_user(Role.SUPER_ADMIN)
    headers = auth_header(superadmin)
    client.post(
        "/api/v1/admin/hospitals/import",
        data={"file": (io.BytesIO(NHI_CSV.encode("utf-8")), "nhi.csv")},
        content_type="multipart/form-data",
        headers=headers,
    )
    resp = client.post(
        "/api/v1/admin/hospitals/import",
        data={"file": (io.BytesIO(MOHW_CSV.encode("utf-8")), "mohw.csv")},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["data"]["errors"] == []
    rows = {
        h["id"]: h
        for h in client.get("/api/v1/admin/hospitals", headers=headers).get_json()["data"]["items"]
    }
    clinic = rows["3501015311"]
    assert clinic["capacity_tier"] == "clinic" and clinic["region"] == "臺北市"
    assert clinic["departments"] == "家庭醫學科,內科"
    ntuh = rows["0401180014"]
    assert ntuh["capacity_tier"] == "medical_center"  # 先前匯入的層級保留
    assert ntuh["departments"] == "家庭醫學科,內科,外科"


def test_import_json_rows_template_format_and_permission(client, make_user, auth_header):
    admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H1")
    rows = [{"id": "H7", "name": "第七醫院", "region": "高雄市", "capacity_tier": "regional"}]
    assert (
        client.post(
            "/api/v1/admin/hospitals/import", json={"rows": rows}, headers=auth_header(admin)
        ).status_code
        == 403
    )
    superadmin = make_user(Role.SUPER_ADMIN)
    resp = client.post(
        "/api/v1/admin/hospitals/import", json={"rows": rows}, headers=auth_header(superadmin)
    )
    assert resp.status_code == 200 and resp.get_json()["data"]["created"] == 1
    template = client.get(
        "/api/v1/admin/hospitals/import/template", headers=auth_header(superadmin)
    )
    assert template.status_code == 200 and "departments" in template.get_data(as_text=True)
