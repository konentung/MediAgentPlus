"""藥局 / 藥品：開放資料匯入、地址解析、依位置找藥局、藥名查詢。"""

from __future__ import annotations

import io
from datetime import datetime

import pytest

from app.domain.enums import Role
from app.services.pharmacy_service import (
    current_slot,
    parse_address,
    parse_opening_hours,
)
from app.utils.clock import TAIPEI

NHI_ROWS = [
    {
        "醫事機構代碼": "5901010076",
        "醫事機構名稱": "榮星藥局",
        "醫事機構種類": "藥劑生自營",
        "電話": "(02)27124696",
        "地址": "臺北市松山區南京東路４段１３３巷５弄１號",
        "終止合約或歇業日期": "20991231",
        "固定看診時段": "星期一上午看診、星期一下午看診、星期一晚上休診、星期二上午看診",
    },
    {
        "醫事機構代碼": "5901010085",
        "醫事機構名稱": "安世藥局",
        "醫事機構種類": "藥師自營",
        "電話": "(02)27133405",
        "地址": "臺北市松山區南京東路４段１３３巷５弄９號",
        "終止合約或歇業日期": "20200101",  # 已終止合約 → 停用
        "固定看診時段": "",
    },
    {
        "醫事機構代碼": "1101010011",
        "醫事機構名稱": "某某診所",
        "醫事機構種類": "診所",
        "地址": "臺北市松山區八德路",
    },
    {
        "醫事機構代碼": "5901020001",
        "醫事機構名稱": "信義藥局",
        "醫事機構種類": "藥師自營",
        "電話": "0227000000",
        "地址": "臺北市信義區松仁路１號",
        "終止合約或歇業日期": "",
        "固定看診時段": "星期日上午看診",
    },
]

MOHW_ROWS = [
    {
        "機構狀態": "開業",
        "機構名稱": "祥全藥局",
        "地址縣市別": "臺北市",
        "地址鄉鎮市區": "松山區",
        "地址街道巷弄號": "信義路二段７９巷",
        "電話": "0223939339",
        "是否為健保特約藥局": "N",
    },
    {
        "機構狀態": "開業",
        "機構名稱": "榮星藥局",
        "地址縣市別": "臺北市",
        "地址鄉鎮市區": "松山區",
        "地址街道巷弄號": "南京東路",
        "電話": "0227124696",
        "是否為健保特約藥局": "Y",  # 已由健保署檔涵蓋 → 略過
    },
    {
        "機構狀態": "歇業",
        "機構名稱": "關門藥局",
        "地址縣市別": "臺北市",
        "地址鄉鎮市區": "松山區",
        "地址街道巷弄號": "x",
        "是否為健保特約藥局": "N",
    },
]


@pytest.mark.parametrize(
    "address, expected",
    [
        ("台灣台北市松山區南京東路四段133巷5弄1號", ("台北市", "松山區")),
        ("105台灣臺北市松山區南京東路", ("台北市", "松山區")),
        ("新竹市東區光復路一段", ("新竹市", "東區")),
        ("彰化縣員林市中山路", ("彰化縣", "員林市")),
        ("高雄市", ("高雄市", None)),
        ("Some English Address", (None, None)),
        ("", (None, None)),
    ],
)
def test_parse_address(address, expected):
    assert parse_address(address) == expected


def test_parse_opening_hours():
    hours = parse_opening_hours("星期一上午看診、星期一下午看診、星期一晚上休診、星期日晚上看診")
    assert hours == {"mon": ["am", "pm"], "sun": ["night"]}
    assert parse_opening_hours("") is None


def test_current_slot():
    assert current_slot(datetime(2026, 9, 28, 9, 0, tzinfo=TAIPEI)) == ("mon", "am")
    assert current_slot(datetime(2026, 9, 27, 20, 0, tzinfo=TAIPEI)) == ("sun", "night")


def test_import_nhi_and_mohw_rows(container):
    result = container.pharmacies.import_pharmacies(NHI_ROWS)
    assert result == {"created": 3, "updated": 0, "skipped": 1}  # 診所略過
    assert container.pharmacy_repo.get("5901010085")["active"] is False
    row = container.pharmacy_repo.get("5901010076")
    assert (row["city"], row["district"]) == ("台北市", "松山區")
    assert row["opening_hours"] == {"mon": ["am", "pm"], "tue": ["am"]}

    result = container.pharmacies.import_pharmacies(MOHW_ROWS)
    assert result == {"created": 1, "updated": 0, "skipped": 2}
    non_nhi = container.pharmacy_repo.search(city="台北市", district="松山區", nhi_only=False)
    assert {p["name"] for p in non_nhi} == {"榮星藥局", "祥全藥局"}

    # 重跑是 upsert，不會重複
    again = container.pharmacies.import_pharmacies(NHI_ROWS)
    assert again["created"] == 0 and again["updated"] == 3


def test_find_by_address_prefers_district_then_city(container, monkeypatch):
    container.pharmacies.import_pharmacies(NHI_ROWS)
    monkeypatch.setattr(
        "app.services.pharmacy_service.now_tw",
        lambda: datetime(2026, 9, 28, 9, 30, tzinfo=TAIPEI),  # 星期一上午
    )
    result = container.pharmacies.find_by_address("台灣台北市松山區南京東路四段")
    assert result["scope"] == "district"
    assert [p["name"] for p in result["pharmacies"]] == ["榮星藥局"]  # 停用的不列
    assert result["pharmacies"][0]["open_now"] is True
    assert result["pharmacies"][0]["today_hours"] == ["上午", "下午"]

    # 該區沒有資料 → 退到同縣市
    result = container.pharmacies.find_by_address("台北市大安區和平東路")
    assert result["scope"] == "city"
    assert {p["name"] for p in result["pharmacies"]} == {"榮星藥局", "信義藥局"}

    assert container.pharmacies.find_by_address("nowhere")["pharmacies"] == []


DRUG_ROWS = [
    {
        "藥品代號": "AC48092100",
        "藥品英文名稱": "Caremod F.C. Tablets 20mg",
        "藥品中文名稱": "撫緒膜衣錠20毫克",
        "成分": "PAROXETINE HYDROCHLORIDE 20 MG",
        "劑型": "膜衣錠",
        "ATC代碼": "N06AB05",
        "有效迄日": "1060331",
    },
    {
        "藥品代號": "AC48092100",
        "藥品英文名稱": "Caremod F.C. Tablets 20mg (new)",
        "藥品中文名稱": "撫緒膜衣錠20毫克",
        "成分": "PAROXETINE HYDROCHLORIDE 20 MG",
        "劑型": "膜衣錠",
        "ATC代碼": "N06AB05",
        "有效迄日": "9991231",  # 較新 → 保留這列
    },
    {
        "藥品代號": "BC23456100",
        "藥品英文名稱": "Metformin HCl Tablets 500mg",
        "藥品中文名稱": "美佛明錠",
        "成分": "METFORMIN HYDROCHLORIDE 500 MG",
        "劑型": "錠劑",
        "ATC代碼": "A10BA02",
        "有效迄日": "9991231",
    },
    {"藥品代號": "", "藥品英文名稱": "broken"},
]


def test_import_and_search_drugs(container):
    result = container.pharmacies.import_drugs(DRUG_ROWS)
    assert result == {"created": 2, "updated": 0, "skipped": 1}
    assert container.drug_repo.get("AC48092100")["name_en"].endswith("(new)")

    hits = container.pharmacies.search_drugs("metformin")
    assert hits[0]["name_en"].startswith("Metformin") and hits[0]["category"] == "消化道及代謝"
    assert container.pharmacies.search_drugs("撫緒")[0]["nhi_code"] == "AC48092100"
    assert container.pharmacies.search_drugs("xyz") == []

    # 辨識出的藥名（含劑量）對回藥品主檔
    match = container.pharmacies.match_drug("Metformin", "Metformin 500mg 膜衣錠")
    assert match["nhi_code"] == "BC23456100"
    assert container.pharmacies.match_drug(None, "不存在的藥") is None


def test_pharmacy_and_drug_api(client, container, make_user, auth_header):
    container.pharmacies.import_pharmacies(NHI_ROWS)
    container.pharmacies.import_drugs(DRUG_ROWS)
    user = make_user()
    resp = client.get(
        "/api/v1/liff/pharmacies?address=台北市松山區南京東路", headers=auth_header(user)
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"]["pharmacies"][0]["name"] == "榮星藥局"

    resp = client.get("/api/v1/liff/drugs?q=metformin", headers=auth_header(user))
    assert resp.status_code == 200 and len(resp.get_json()["data"]) == 1
    assert client.get("/api/v1/liff/drugs?q=m", headers=auth_header(user)).status_code == 422
    assert client.get("/api/v1/liff/drugs?q=metformin").status_code == 401


def _csv_bytes(rows: list[dict]) -> io.BytesIO:
    import csv

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return io.BytesIO(("﻿" + buf.getvalue()).encode("utf-8"))


def test_super_admin_imports_reference_data_via_admin_api(client, make_user, auth_header):
    superadmin = make_user(Role.SUPER_ADMIN)
    headers = auth_header(superadmin)

    resp = client.post(
        "/api/v1/admin/pharmacies/import",
        data={"file": (_csv_bytes(NHI_ROWS), "pharmacies.csv")},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["data"] == {"created": 3, "updated": 0, "skipped": 1}

    resp = client.post(
        "/api/v1/admin/drugs/import",
        data={"file": (_csv_bytes(DRUG_ROWS), "drugs.csv")},
        content_type="multipart/form-data",
        headers=headers,
    )
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["data"]["created"] == 2

    stats = client.get("/api/v1/admin/reference-data", headers=headers).get_json()["data"]
    assert stats == {"pharmacies": 3, "drugs": 2}

    # 也接受 JSON rows；空檔案 422
    resp = client.post("/api/v1/admin/pharmacies/import", json={"rows": MOHW_ROWS}, headers=headers)
    assert resp.status_code == 200 and resp.get_json()["data"]["created"] == 1
    assert (
        client.post("/api/v1/admin/drugs/import", json={"rows": []}, headers=headers).status_code
        == 422
    )


def test_reference_data_import_is_super_admin_only(client, make_user, auth_header):
    admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H1")
    for path in ("/api/v1/admin/pharmacies/import", "/api/v1/admin/drugs/import"):
        resp = client.post(path, json={"rows": NHI_ROWS}, headers=auth_header(admin))
        assert resp.status_code == 403
    assert client.get("/api/v1/admin/reference-data", headers=auth_header(admin)).status_code == 403
    assert client.get("/api/v1/admin/reference-data").status_code == 401
