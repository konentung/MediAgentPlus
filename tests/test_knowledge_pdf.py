"""衛教資訊 PDF 匯入：文字擷取與整理、依院所歸屬、片段大小、重切、自動算向量、各院所統計。"""

from __future__ import annotations

import io

import pytest

from app.core.exceptions import ValidationError
from app.domain.enums import Role
from app.utils.pdf import PdfText, extract_pdf_text, normalize_pdf_pages

IMPORT_URL = "/api/v1/admin/knowledge/jobs/pdf-import"
RECHUNK_URL = "/api/v1/admin/knowledge/jobs/rechunk"

_PARAGRAPHS = (
    "高血壓病人要規律服藥並每天量血壓，血壓控制好可以減少中風與心肌梗塞的風險。",
    "早晚各量一次，量之前先靜坐五分鐘，記錄在血壓手冊帶回診時給醫師看。",
    "飲食要少鹽少油，每週運動三次、每次三十分鐘，戒菸並避免熬夜。",
    "血壓超過一百八十且頭痛、胸痛或呼吸困難時，請儘快就醫不要自行加藥。",
    "降血壓藥要每天固定時間吃，忘記吃藥不要一次補吃兩倍的劑量。",
    "起身時動作放慢，避免姿勢性低血壓造成頭暈跌倒，洗澡水溫不要太高。",
)
# 夠長、切得出好幾個片段的內文（每段各自成行，模擬 PDF 擷取後整理好的段落）
LONG_TEXT = "\n".join(f"{p}{p}{p}" for p in _PARAGRAPHS)
SHORT_TEXT = "\n".join(_PARAGRAPHS[:2])


def _pdf(pages: list[list[str]]) -> bytes:
    """最小的文字型 PDF（Helvetica，只能放 ASCII），每頁一行一行往下排。"""
    page_ids = [4 + index * 2 for index in range(len(pages))]
    kids = " ".join(f"{i} 0 R" for i in page_ids)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for page_id, lines in zip(page_ids, pages, strict=True):
        ops = ["BT /F1 12 Tf 72 720 Td 16 TL", *(f"({line}) Tj T*" for line in lines), "ET"]
        stream = "\n".join(ops).encode()
        objects.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>"
            ).encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


@pytest.fixture()
def text_pdfs(monkeypatch, container):
    """上傳檔的位元組直接當成 UTF-8 文字（換頁用 \\f），略過真正的 PDF 解析；背景工作改同步。"""
    from app.services import knowledge_service

    def fake_extract(data: bytes) -> PdfText:
        pages = data.decode("utf-8").split("\f")
        text = normalize_pdf_pages(pages)
        if not text:
            raise ValidationError("PDF 內沒有可擷取的文字")
        return PdfText(text=text, pages=len(pages))

    monkeypatch.setattr(knowledge_service, "extract_pdf_text", fake_extract)
    container.knowledge_jobs.spawn = lambda fn: fn()


def _upload(client, auth_header, user, files: dict[str, str | bytes], **fields):
    data = {
        "files": [
            (io.BytesIO(body.encode() if isinstance(body, str) else body), name)
            for name, body in files.items()
        ],
        **fields,
    }
    return client.post(IMPORT_URL, data=data, headers=auth_header(user))


def _job(container, user, response):
    return container.knowledge_jobs.get(
        container.users.to_principal(user), response.get_json()["data"]["id"]
    )


# ---- 文字擷取 ----
def test_normalize_pdf_pages_unwraps_lines_and_drops_page_edges():
    header = "某某醫院 衛教單張"
    pages = [
        f"{header}\n高血壓居家照護\n高血壓病人要規律服藥並每天量血壓血壓控制好可以\n"
        "減少中風與心肌梗塞的風險也能保護腎臟功能所以一定\n要持續追蹤。\n第 1 頁，共 3 頁",
        f"{header}\n一、每天量血壓\n早晚各量一次，量之前先靜坐五分鐘並記錄在血壓手冊\n帶回診時給醫師看。\n- 2 -",
        f"{header}\n二、什麼情況要就醫\n1. 血壓超過 180/110 且頭痛。\n2. 胸痛或呼吸困難。\n3",
    ]
    paragraphs = normalize_pdf_pages(pages).split("\n\n")
    assert header not in paragraphs and not any("頁" in p and "共" in p for p in paragraphs)
    # 排版折行接回同一段；標題、條列項目各自成段
    assert paragraphs[:2] == [
        "高血壓居家照護",
        "高血壓病人要規律服藥並每天量血壓血壓控制好可以減少中風與心肌梗塞的風險"
        "也能保護腎臟功能所以一定要持續追蹤。",
    ]
    assert "早晚各量一次，量之前先靜坐五分鐘並記錄在血壓手冊帶回診時給醫師看。" in paragraphs
    assert paragraphs[-2:] == ["1. 血壓超過 180/110 且頭痛。", "2. 胸痛或呼吸困難。"]
    assert normalize_pdf_pages(["", "  \n 12 \n"]) == ""
    # 字型沒有文字對應表造成的亂碼行（混了好幾種不相干的文字）整行拿掉；
    # 正常的中文、英文、希臘字母、單一外語（多語衛教）都保留
    cleaned = normalize_pdf_pages(
        [
            "肥胖的定義與處置。\nꅾ䏞涸麌⹛剤ⵄ倴㷸留涸聞聘남냛涮㾝\nйϞዚଟɰึጩष\n"
            "每天攝取 β-胡蘿蔔素 5 μg。\nBMI ≥ 27 kg/m² is obese.\nกรุณาล้างมือให้สะอาด"
        ]
    )
    assert "ꅾ" not in cleaned and "남" not in cleaned and "й" not in cleaned
    for kept in ("肥胖的定義與處置。", "β-胡蘿蔔素 5 μg", "is obese.", "กรุณาล้างมือให้สะอาด"):
        assert kept in cleaned
    # NUL 等控制字元要去掉：PostgreSQL 的文字欄位不接受 0x00，留著整份文件會寫不進去
    assert (
        normalize_pdf_pages(["血壓\x00控制\x07良好。\n飲食\x00清淡。"])
        == "血壓控制良好。\n\n飲食清淡。"
    )
    # CJK 相容表意文字（U+F900–FAFF）也一樣：長得跟一般漢字相同、字碼不同，不換回來斷詞與檢索都會錯
    assert normalize_pdf_pages(["\uf934人失智\uf967是正常\uf934化，\uf92d院檢查\uf98a行。"]) == (
        "老人失智不是正常老化，來院檢查力行。"
    )
    # PDF 字型常把漢字抽成康熙部首區的字元（看起來一樣、字碼不同）→ 換回一般漢字；全形標點不動
    assert normalize_pdf_pages(["\u2fbc\u2f8e壓病\u2f08，要量\u2f8e壓：早晚各\u2f00次。"]) == (
        "高血壓病人，要量血壓：早晚各一次。"
    )


def test_extract_pdf_text_reads_real_pdf_and_rejects_unreadable():
    pdf = extract_pdf_text(
        _pdf([["Blood pressure care", "Measure twice a day."], ["Call your doctor if dizzy."]])
    )
    assert pdf.pages == 2
    assert "Blood pressure care" in pdf.text and "Call your doctor if dizzy." in pdf.text
    with pytest.raises(ValidationError, match="無法讀取"):
        extract_pdf_text(b"this is not a pdf")
    with pytest.raises(ValidationError, match="沒有可擷取的文字"):  # 沒有文字層（掃描檔）
        extract_pdf_text(_pdf([[]]))


def test_garbled_chunks_are_not_indexed(container, make_user):
    """表格被抽成一格一兩個亂碼字，擷取時逐行看不出來；併成片段後把亂碼行拿掉、正常內容留著。"""
    admin = container.users.to_principal(make_user(Role.SUPER_ADMIN))
    cells = ["ং", "Κ", "षઃf", "໊", "ଣ͟", "ߛ", "ᅰ", "༟๕", "፯d", "ਗਗ໔", "ᙆৃf", "૩д"]
    garbage = "\n\n".join(cells * 3)
    doc = container.knowledge.create(
        admin,
        {
            "title": "代謝症候群",
            "category": "education",
            "content": (
                "代謝症候群是腹部肥胖、血壓偏高、血糖偏高與血脂異常同時出現的狀態，"
                "平常要控制腰圍、少吃含糖飲料，並且每週規律運動一百五十分鐘以上。"
                f"\n\n{garbage}"
            ),
        },
    )
    listed = next(d for d in container.knowledge.list(admin) if d["id"] == doc["id"])
    assert listed["chunk_count"] >= 1
    hits = container.knowledge.retrieve("代謝症候群 腰圍", hospital_ids=[None])
    assert hits and "控制腰圍" in hits[0]["content"]
    assert all("ং" not in h["content"] and "ਗ" not in h["content"] for h in hits)


# ---- 匯入 ----
def test_pdf_import_belongs_to_uploader_hospital(
    client, container, make_user, auth_header, text_pdfs
):
    h1_admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H1")
    h2_admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H2")
    files = {"高血壓居家照護.pdf": LONG_TEXT, "量血壓須知.pdf": SHORT_TEXT}

    # 權限與參數：沒有衛教管理權限 / 指定別家院所 → 403；片段大小不合法、不是 PDF → 422
    staff = make_user(Role.HOSPITAL_STAFF, hospital_id="H1")
    assert _upload(client, auth_header, staff, files).status_code == 403
    assert _upload(client, auth_header, h1_admin, files, hospital_id="H2").status_code == 403
    assert _upload(client, auth_header, h1_admin, files, chunk_size="huge").status_code == 422
    assert _upload(client, auth_header, h1_admin, {"notes.txt": "x"}).status_code == 422

    resp = _upload(client, auth_header, h1_admin, files, department="心臟內科", chunk_size="small")
    assert resp.status_code == 202
    job = _job(container, h1_admin, resp)
    assert job["kind"] == "pdf_import" and job["status"] == "succeeded"
    assert job["params"]["hospital_id"] == "H1" and "path" not in job["params"]
    result = job["result"]
    assert result["created"] == 2 and result["updated"] == 0 and result["errors"] == []
    # 匯入後自動算好本院片段的向量
    assert result["embedded"] == result["chunks"] > 0
    assert not (container.knowledge_jobs._upload_dir / job["id"]).exists()  # 暫存目錄已刪

    # 一個 PDF 一篇：標題 = 檔名、歸本院、科別與片段大小照上傳時選的
    docs = {d["title"]: d for d in container.knowledge.list(container.users.to_principal(h1_admin))}
    assert set(docs) == {"高血壓居家照護", "量血壓須知"}
    doc = docs["高血壓居家照護"]
    assert doc["hospital_id"] == "H1" and doc["category"] == "education"
    assert doc["department"] == "心臟內科" and doc["tags"] == ["心臟內科"]
    assert doc["source"] == "高血壓居家照護.pdf" and doc["meta"]["hospital_name"] == "第一醫院"
    assert doc["chunk_size"] == 300 and doc["chunk_count"] > 1
    assert sum(d["chunk_count"] for d in docs.values()) == result["chunks"]
    assert container.knowledge.list(container.users.to_principal(h2_admin)) == []

    # 檢索：文件歸本院管理，但所有院所的病人都查得到（共用池）；明確限定範圍時才只查那個範圍
    def owners(**kwargs):
        return {h["hospital_id"] for h in container.knowledge.retrieve("血壓要怎麼量", **kwargs)}

    assert owners(prefer_hospital_ids=["H2"]) == {"H1"}
    assert owners(hospital_ids=[None, "H1"]) == {"H1"} and owners(hospital_ids=["H2"]) == set()

    # 同院所同檔名重傳 = 更新（片段大小換成這次選的）；讀不到文字的檔列在 errors，不影響其他檔
    resp = _upload(
        client,
        auth_header,
        h1_admin,
        {"高血壓居家照護.pdf": LONG_TEXT, "掃描檔.pdf": "\f"},
        chunk_size="large",
    )
    result = _job(container, h1_admin, resp)["result"]
    assert result["created"] == 0 and result["updated"] == 1
    assert [e["file"] for e in result["errors"]] == ["掃描檔.pdf"]
    docs = {d["title"]: d for d in container.knowledge.list(container.users.to_principal(h1_admin))}
    assert len(docs) == 2 and docs["高血壓居家照護"]["chunk_size"] == 600
    assert docs["高血壓居家照護"]["chunk_count"] < doc["chunk_count"]

    # 進度 / 統計：院所管理員只看得到自己院所的工作
    def overview(user):
        return client.get("/api/v1/admin/knowledge/jobs", headers=auth_header(user)).get_json()[
            "data"
        ]

    own, other = overview(h1_admin), overview(h2_admin)
    assert own["stats"]["documents"] == 2 and len(own["recent"]) == 2
    assert own["departments"] == ["心臟內科"]
    assert [o["key"] for o in own["chunk_options"]["sizes"]] == ["small", "medium", "large"]
    assert other["stats"]["documents"] == 0 and other["recent"] == [] and other["departments"] == []
    job_url = f"/api/v1/admin/knowledge/jobs/{job['id']}"
    assert client.get(job_url, headers=auth_header(h1_admin)).status_code == 200
    assert client.get(job_url, headers=auth_header(h2_admin)).status_code == 422


def test_pdf_import_reads_real_pdf_end_to_end(client, container, make_user, auth_header):
    container.knowledge_jobs.spawn = lambda fn: fn()
    superadmin = make_user(Role.SUPER_ADMIN)
    body = _pdf([["Blood pressure care", "Measure your blood pressure twice a day."]])
    # 系統管理員：指定院所；不存在的院所代碼 → 422；不給院所 = 通用
    assert (
        _upload(client, auth_header, superadmin, {"bp.pdf": body}, hospital_id="NOPE").status_code
        == 422
    )
    resp = _upload(client, auth_header, superadmin, {"bp.pdf": body}, hospital_id="H2")
    assert _job(container, superadmin, resp)["result"]["created"] == 1
    resp = _upload(client, auth_header, superadmin, {"bp.pdf": body})
    assert _job(container, superadmin, resp)["params"]["hospital_id"] is None
    docs = container.knowledge.list(container.users.to_principal(superadmin))
    assert {d["hospital_id"] for d in docs} == {"H2", None}  # 同檔名在不同範圍各一份
    assert {d["title"] for d in docs} == {"bp"} and docs[0]["meta"]["pages"] == 1
    hits = container.knowledge.retrieve("blood pressure", hospital_ids=["H2"])
    assert hits and "Measure your blood pressure twice a day." in hits[0]["content"]


def test_pdf_import_survives_database_error_on_one_file(
    client, container, make_user, auth_header, text_pdfs
):
    """一個檔案寫入資料庫失敗：交易還原、記成錯誤，其他檔案照常匯入，工作正常結束。"""
    from app.extensions import db
    from app.models.knowledge import KnowledgeDocumentModel

    h1_admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H1")
    original = container.knowledge_repo.create

    def flaky_create(**values):
        if values["title"] == "壞掉的":  # 模擬資料庫拒絕這一筆（真實情況：內文夾了 NUL）
            db.session.add(KnowledgeDocumentModel(title=None, content=values["content"]))
            db.session.commit()
        return original(**values)

    container.knowledge_repo.create = flaky_create
    files = {"高血壓.pdf": SHORT_TEXT, "壞掉的.pdf": SHORT_TEXT, "量血壓.pdf": SHORT_TEXT}
    job = _job(container, h1_admin, _upload(client, auth_header, h1_admin, files))
    assert job["status"] == "succeeded"
    assert job["result"]["created"] == 2 and job["result"]["embedded"] == job["result"]["chunks"]
    assert [e["file"] for e in job["result"]["errors"]] == ["壞掉的.pdf"]
    docs = container.knowledge.list(container.users.to_principal(h1_admin))
    assert {d["title"] for d in docs} == {"高血壓", "量血壓"}


def test_job_is_marked_failed_after_database_error(
    client, container, make_user, auth_header, text_pdfs
):
    """整個工作因資料庫錯誤中斷時要落到 failed，不能卡在 running 擋住之後的匯入。"""
    from app.extensions import db
    from app.models.knowledge import KnowledgeDocumentModel

    h1_admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H1")

    def broken_import(*args, **kwargs):
        db.session.add(KnowledgeDocumentModel(title=None, content="x"))
        db.session.commit()

    container.knowledge.import_pdfs = broken_import
    job = _job(
        container, h1_admin, _upload(client, auth_header, h1_admin, {"高血壓.pdf": SHORT_TEXT})
    )
    assert job["status"] == "failed" and job["error"]
    overview = client.get("/api/v1/admin/knowledge/jobs", headers=auth_header(h1_admin))
    assert overview.get_json()["data"]["busy"] is False


def test_pdf_import_succeeds_when_embedding_unavailable(
    client, container, make_user, auth_header, text_pdfs
):
    h1_admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H1")
    container.llm_client.embedding_model = None  # 未設定向量模型
    try:
        resp = _upload(client, auth_header, h1_admin, {"量血壓須知.pdf": SHORT_TEXT})
        job = _job(container, h1_admin, resp)
    finally:
        container.llm_client.embedding_model = "fake-embedding"
    # 片段照存、關鍵字檢索可用，只是沒有向量；原因記在結果裡
    assert job["status"] == "succeeded" and job["result"]["created"] == 1
    assert job["result"]["embedded"] == 0 and "EMBEDDING_MODEL" in job["result"]["embed_error"]
    assert container.knowledge.retrieve("量血壓", hospital_ids=["H1"])


# ---- 重切 ----
def test_hospital_admin_rechunk_changes_size_for_own_hospital_only(
    client, container, make_user, auth_header, text_pdfs
):
    superadmin = make_user(Role.SUPER_ADMIN)
    h1_admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H1")
    h2_admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H2")
    for user in (h1_admin, h2_admin):
        resp = _upload(client, auth_header, user, {"高血壓.pdf": LONG_TEXT}, chunk_size="large")
        assert resp.status_code == 202

    def doc_of(user):
        return container.knowledge.list(container.users.to_principal(user))[0]

    before_h1, before_h2 = doc_of(h1_admin), doc_of(h2_admin)
    assert before_h1["chunk_size"] == 600

    def rechunk(user, body=None):
        return client.post(RECHUNK_URL, json=body or {}, headers=auth_header(user))

    assert rechunk(make_user(Role.HOSPITAL_STAFF, hospital_id="H1")).status_code == 403
    assert rechunk(h1_admin, {"chunk_size": "huge"}).status_code == 422
    resp = rechunk(h1_admin, {"chunk_size": "small"})
    assert resp.status_code == 202
    job = _job(container, h1_admin, resp)
    assert job["params"] == {"documents": 1, "hospital_id": "H1", "chunk_size": "small"}
    # 本院文件換成小片段（變多）、做完自動重算向量；別家的不動
    after_h1 = doc_of(h1_admin)
    assert after_h1["chunk_size"] == 300 and after_h1["chunk_count"] > before_h1["chunk_count"]
    assert job["result"]["chunks"] == job["result"]["embedded"] == after_h1["chunk_count"]
    assert doc_of(h2_admin)["chunk_count"] == before_h2["chunk_count"]
    assert doc_of(h2_admin)["chunk_size"] == 600
    stats = container.knowledge.stats()
    assert stats["embedded"] == stats["chunks"]

    # 系統管理員：重建全部、不帶大小 = 各文件維持原本的大小；全庫的向量要另外計算
    resp = rechunk(superadmin)
    result = _job(container, superadmin, resp)["result"]
    assert result == {"documents": 2, "chunks": stats["chunks"]}
    assert doc_of(h1_admin)["chunk_size"] == 300 and doc_of(h2_admin)["chunk_size"] == 600
    assert container.knowledge.stats()["embedded"] == 0
    # 院所管理員不能按全庫「計算向量」
    embed_url = "/api/v1/admin/knowledge/jobs/embed"
    assert client.post(embed_url, headers=auth_header(h1_admin)).status_code == 403
    assert client.post(embed_url, headers=auth_header(superadmin)).status_code == 202
    assert container.knowledge.stats()["embedded"] == stats["chunks"]


# ---- 各院所統計（系統管理員）----
def test_hospital_overview_lists_chunk_counts_per_hospital(
    client, container, make_user, auth_header, text_pdfs
):
    superadmin = make_user(Role.SUPER_ADMIN)
    h1_admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H1")
    h2_admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H2")
    _upload(client, auth_header, h1_admin, {"高血壓.pdf": LONG_TEXT, "量血壓.pdf": SHORT_TEXT})
    _upload(client, auth_header, h2_admin, {"量血壓.pdf": SHORT_TEXT})
    container.knowledge.create(
        container.users.to_principal(superadmin),
        {"title": "服務說明", "content": "這是通用的服務說明文件", "category": "service"},
    )

    def overview(user):
        url = "/api/v1/admin/knowledge/hospitals"
        return client.get(url, headers=auth_header(user)).get_json()["data"]

    rows = {r["hospital_id"]: r for r in overview(superadmin)}
    assert set(rows) == {"H1", "H2", None}
    assert rows["H1"]["hospital_name"] == "第一醫院" and rows["H1"]["documents"] == 2
    assert rows["H2"]["documents"] == 1 and rows[None]["documents"] == 1
    # 片段數 = 該院所每篇文件片段數的總和（點進去看到的就是這些）
    docs = container.knowledge.list(container.users.to_principal(superadmin))
    for hospital_id, row in rows.items():
        counts = [d["chunk_count"] for d in docs if d["hospital_id"] == hospital_id]
        assert row["chunks"] == sum(counts) > 0 and row["embedded"] == row["chunks"]
    assert rows["H1"]["chunks"] > rows["H2"]["chunks"]
    assert overview(superadmin)[0]["hospital_id"] == "H1"  # 片段多的排前面
    # 院所管理員只有自己院所那一列
    assert [r["hospital_id"] for r in overview(h1_admin)] == ["H1"]
    staff = make_user(Role.HOSPITAL_STAFF, hospital_id="H1")
    url = "/api/v1/admin/knowledge/hospitals"
    assert client.get(url, headers=auth_header(staff)).status_code == 403
