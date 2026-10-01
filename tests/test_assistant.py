"""AI 對話助理：檢索（jieba + BM25）、知識庫 CRUD 與院所隔離、脈絡組裝、事件 → 模組對應、退路。"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from linebot.v3.messaging import FlexMessage, TextMessage

from app.core.exceptions import ExternalServiceError
from app.domain.enums import BindingStatus, Intent, Role
from app.domain.retrieval import (
    BM25Index,
    chunk_document,
    chunk_text,
    clean_lines,
    collect_boilerplate,
    tokenize,
)
from app.services.knowledge_service import parse_markdown
from tests.test_webhook import _event

SEED_DIR = Path(__file__).resolve().parents[1] / "data" / "knowledge"


def _text(post_line_event, text, user_id="U1"):
    return post_line_event(
        _event(
            "message",
            user_id=user_id,
            message={"type": "text", "id": "m1", "text": text, "quoteToken": "q"},
        )
    )


def _system_actor(container, make_user, role=Role.SUPER_ADMIN, hospital_id=None):
    return container.users.to_principal(make_user(role, hospital_id=hospital_id))


# ---- 檢索 ----
def test_tokenize_mixed_chinese_english():
    tokens = tokenize("我頭痛兩天了，Metformin 500mg 要飯後吃嗎？")
    assert "頭痛" in tokens and "metformin" in tokens and "飯後" in tokens
    assert "我" not in tokens and "嗎" not in tokens


def test_bm25_ranks_relevant_chunk_first():
    index = BM25Index.build(
        [
            ("a", tokenize("慢性病連續處方箋（慢箋）可以分次領藥 每次最多 30 天")),
            ("b", tokenize("發燒超過三天 請儘速就醫 家醫科")),
            ("c", tokenize("服藥提醒 到點會收到 LINE 訊息")),
        ]
    )
    assert index.search("慢箋可以領幾次")[0][0] == "a"
    assert index.search("發燒要看哪一科")[0][0] == "b"
    assert index.search("完全無關的 xyz") == []


def test_chunk_text_keeps_headings_with_paragraphs():
    text = "# 標題\n\n## 小節一\n\n第一段內容。\n\n## 小節二\n\n第二段內容。"
    chunks = chunk_text(text, max_chars=20)
    assert chunks[0].startswith("小節一：第一段") and chunks[1].startswith("小節二：第二段")


def test_chunk_document_heading_path_lists_tables_and_noise():
    text = (
        "人工髖關節照護\n\n首頁 > 衛教園地 > 衛教單張\n\nQrcode\n\n108-12-09\n\n"
        "手術後須維持正確的姿勢，執行復健運動。\n\n"
        "一、注意事項\n\n（一）飲食\n\n多攝取高蛋白質食物，以促進傷口癒合。\n\n"
        "（二）活動\n\n有下列情況請儘快就醫：\n\n1. 傷口紅腫熱痛。\n\n2. 發燒超過 38 度。\n\n"
        "二、常見問題\n\nQ：多久可以洗澡？\n\n拆線後兩天可以淋浴，不要泡澡。\n\n"
        "項目\n\n數值\n\n血壓\n\n120\n\n心跳\n\n72\n\n體溫\n\n36.5\n\n"
        "1. 高血壓\n\n2. 糖尿病\n\n3. 腎臟病\n\n以上為常見的危險因子。"
    )
    chunks = chunk_document(text, title="人工髖關節照護", max_chars=60)
    joined = "\n".join(c.content for c in chunks)
    # 標題重複行、麵包屑、Qrcode、日期都不在片段裡；min_chars 很小，所以每段獨立
    for noise in ("首頁", "Qrcode", "108-12-09", "人工髖關節照護："):
        assert noise not in joined
    by_heading = {c.heading: c for c in chunks}
    assert by_heading["注意事項 › 飲食"].content.startswith("注意事項 › 飲食：多攝取高蛋白質")
    care = by_heading["注意事項 › 活動"]
    assert (
        care.kind == "list" and "有下列情況請儘快就醫：\n1. 傷口紅腫熱痛。\n2. 發燒" in care.content
    )
    qa = next(c for c in chunks if c.kind == "qa")
    assert qa.heading == "常見問題" and "Q：多久可以洗澡？\n拆線後兩天" in qa.content
    # 表格攤平成「｜」分隔（跟前一段合併時 kind 沿用前一段）
    assert any("項目｜數值｜血壓｜120｜心跳｜72｜體溫｜36.5" in c.content for c in chunks)
    table = chunk_document("表格\n\n" + "\n\n".join(["項目", "數值", "血壓", "120", "心跳", "72"]))
    assert len(table) == 1 and table[0].kind == "table"
    # 連續的「1. 2. 3.」短行是條列不是標題
    risk = next(c for c in chunks if "1. 高血壓" in c.content)
    assert risk.kind == "list" and "以上為常見的危險因子" in risk.content
    assert all(c.heading != "高血壓" for c in chunks)


def test_chunk_document_recursive_split_overlap_and_min_merge():
    sentences = [f"第{i}句說明服藥後要注意的事情與觀察重點。" for i in range(1, 41)]
    text = "以下是服藥後的注意事項。\n\n服藥注意\n\n" + "".join(sentences) + "\n\n結尾。"
    chunks = chunk_document(text, max_chars=200)
    assert len(chunks) > 3
    for c in chunks:
        assert len(c.content) <= 300 and c.content.rstrip().endswith(
            "。"
        )  # 硬上限 1.5 倍、句界收尾
    # 相鄰片段重疊最後一句
    first_tail = chunks[0].content.rstrip().split("。")[-2] + "。"
    assert chunks[1].content.startswith("服藥注意：" + first_tail)
    # 太短的尾巴（結尾。）併回前一段而不是自成一段
    assert "結尾。" in chunks[-1].content and len(chunks[-1].content) > 40
    # 太短的開頭段落把第二段併進來（沿用第一段沒有標題），其餘都在「服藥注意」小節
    assert chunks[0].heading == "" and "服藥注意：第1句" in chunks[0].content
    assert all(c.heading == "服藥注意" for c in chunks[1:])


def test_collect_boilerplate_keeps_section_headings():
    paragraph = "血壓要每天早晚各量一次，量之前先靜坐五分鐘，記錄在血壓手冊帶回診時給醫師看。"
    docs = [
        ["衛教資訊", "聯絡我們", "", "注意事項", paragraph, "", "參考來源", "聯絡我們"]
        for _ in range(40)
    ]
    boilerplate = collect_boilerplate(docs)
    assert {
        "衛教資訊",
        "聯絡我們",
        "參考來源",
    } <= boilerplate  # 頁尾「參考來源」後面接導覽，也是樣板
    assert "注意事項" not in boilerplate and paragraph not in boilerplate
    lines = clean_lines("\n".join(docs[0]), boilerplate=boilerplate)
    assert lines == ["注意事項", paragraph]


def test_parse_markdown_front_matter():
    fields = parse_markdown(
        "---\ntitle: 慢箋\ncategory: medication\ntags: 慢箋, 領藥\n---\n# 忽略\n內文",
        default_title="x",
    )
    assert (
        fields["title"] == "慢箋"
        and fields["category"] == "medication"
        and fields["content"].startswith("# 忽略")
    )
    fields = parse_markdown("# 用標題\n\n內文", default_title="x")
    assert fields["title"] == "用標題" and fields["category"] == "faq"


# ---- 知識庫 ----
def test_seed_import_and_retrieve(container, make_user):
    actor = _system_actor(container, make_user)
    result = container.knowledge.import_path(actor, SEED_DIR)
    assert result["errors"] == [] and result["created"] >= 7
    again = container.knowledge.import_path(actor, SEED_DIR)
    assert again["created"] == 0 and again["updated"] == result["created"]  # 重匯更新、不重複
    hits = container.knowledge.retrieve("慢性病連續處方箋可以領幾次", hospital_ids=[None])
    assert hits and "慢" in hits[0]["title"]
    hits = container.knowledge.retrieve("漏吃一次藥怎麼辦", hospital_ids=[None])
    assert any("漏吃" in h["content"] for h in hits)


def test_hospital_documents_are_scoped(container, make_user):
    superadmin = _system_actor(container, make_user)
    container.knowledge.create(
        superadmin, {"title": "通用", "content": "通用內容 停車場 資訊", "category": "faq"}
    )
    h1_staff = _system_actor(container, make_user, Role.MEDICAL_STAFF, "H1")
    container.knowledge.create(
        h1_staff,
        {"title": "第一醫院停車", "content": "第一醫院 停車場 在地下二樓", "category": "hospital"},
    )
    h2_staff = _system_actor(container, make_user, Role.MEDICAL_STAFF, "H2")
    assert [d["title"] for d in container.knowledge.list(h2_staff)] == ["通用"]
    assert {d["title"] for d in container.knowledge.list(h1_staff)} == {"第一醫院停車", "通用"}
    # 檢索：沒有院所 → 只看到通用；H1 → 也看到院所文件
    titles = {h["title"] for h in container.knowledge.retrieve("停車場", hospital_ids=[None])}
    assert titles == {"通用"}
    titles = {h["title"] for h in container.knowledge.retrieve("停車場", hospital_ids=[None, "H1"])}
    assert titles == {"通用", "第一醫院停車"}


def test_knowledge_admin_api_permissions(client, make_user, auth_header):
    superadmin = make_user(Role.SUPER_ADMIN)
    h1_admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H1")
    agent = make_user(Role.HOSPITAL_STAFF, hospital_id="H1")
    patient = make_user(Role.PATIENT)
    body = {"title": "通用 FAQ", "content": "內容", "category": "faq"}
    assert (
        client.post("/api/v1/admin/knowledge", json=body, headers=auth_header(patient)).status_code
        == 403
    )
    assert (
        client.post("/api/v1/admin/knowledge", json=body, headers=auth_header(agent)).status_code
        == 403
    )
    resp = client.post("/api/v1/admin/knowledge", json=body, headers=auth_header(superadmin))
    assert resp.status_code == 201 and resp.get_json()["data"]["hospital_id"] is None
    generic_id = resp.get_json()["data"]["id"]
    # 院所管理員：建立的一律綁自己院所；不能改通用文件
    resp = client.post(
        "/api/v1/admin/knowledge",
        json={**body, "title": "本院", "hospital_id": "H2"},
        headers=auth_header(h1_admin),
    )
    assert resp.status_code == 403
    resp = client.post(
        "/api/v1/admin/knowledge", json={**body, "title": "本院"}, headers=auth_header(h1_admin)
    )
    assert resp.status_code == 201 and resp.get_json()["data"]["hospital_id"] == "H1"
    own_id = resp.get_json()["data"]["id"]
    assert (
        client.patch(
            f"/api/v1/admin/knowledge/{generic_id}",
            json={"title": "x"},
            headers=auth_header(h1_admin),
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/v1/admin/knowledge/{own_id}",
            json={"active": False},
            headers=auth_header(h1_admin),
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/admin/knowledge",
            json={**body, "category": "nope"},
            headers=auth_header(superadmin),
        ).status_code
        == 422
    )
    # 讀取：客服看得到，試搜可用
    rows = client.get("/api/v1/admin/knowledge", headers=auth_header(agent)).get_json()["data"]
    assert {r["title"] for r in rows} == {"通用 FAQ", "本院"}
    assert (
        client.get("/api/v1/admin/knowledge/search?q=FAQ", headers=auth_header(agent)).status_code
        == 200
    )
    # 匯入
    md = "---\ntitle: 匯入的\ncategory: education\n---\n內文 傷口 照護"
    resp = client.post(
        "/api/v1/admin/knowledge/import",
        data={"files": (io.BytesIO(md.encode()), "care.md")},
        headers=auth_header(h1_admin),
    )
    assert resp.status_code == 200 and resp.get_json()["data"]["created"] == 1
    resp = client.post(
        "/api/v1/admin/knowledge/import",
        data={"files": (io.BytesIO(md.encode()), "care.md")},
        headers=auth_header(h1_admin),
    )
    assert resp.get_json()["data"]["updated"] == 1
    assert (
        client.delete(
            f"/api/v1/admin/knowledge/{own_id}", headers=auth_header(h1_admin)
        ).status_code
        == 200
    )
    assert client.get("/admin/knowledge").status_code == 200


# ---- 脈絡與決策 ----
def test_context_includes_knowledge_live_data_and_history(container, make_user, post_line_event):
    actor = _system_actor(container, make_user)
    container.knowledge.import_path(actor, SEED_DIR)
    container.pharmacies.import_drugs(
        [
            {
                "藥品代號": "A1",
                "藥品英文名稱": "METFORMIN HCL",
                "藥品中文名稱": "美佛明",
                "成分": "METFORMIN",
                "ATC代碼": "A10BA02",
                "有效迄日": "20991231",
            }
        ]
    )
    container.triage.poll()
    principal = container.users.to_principal(make_user(Role.GUEST, line_user_id="Uctx"))
    context = container.conversation.build_context(
        principal, "台北市急診很塞嗎？我在吃 metformin 可以去領慢箋嗎"
    )
    assert context.hits and any(("慢" in h["title"] or "服藥" in h["title"]) for h in context.hits)
    assert any("台北市 急診即時狀況" in b and "臺大" in b for b in context.live_blocks)
    assert any("美佛明" in b for b in context.live_blocks)
    prompt = container.conversation.build_prompt("x", context)
    assert "【知識庫片段】" in prompt and "[K1]" in prompt and "【即時資料】" in prompt
    # 對話紀錄：decide 之後會記住，並帶進下一次 prompt
    container.conversation.decide(principal, "哈囉")
    context = container.conversation.build_context(principal, "再問一次")
    assert context.history[0]["text"] == "哈囉" and context.history[1]["role"] == "assistant"
    assert "【最近對話】" in container.conversation.build_prompt("再問一次", context)


def test_decide_validates_and_trims(container, make_user):
    principal = container.users.to_principal(make_user(Role.GUEST, line_user_id="Ud"))
    container.llm_client.default = {
        "event": "faq",
        "answer": "x" * 1000,
        "actions": ["find_hospital", "find_hospital", "reminder_menu"],
    }
    decision = container.conversation.decide(principal, "隨便問")
    assert len(decision.answer) == 400 and decision.actions == ["find_hospital", "reminder_menu"]
    container.llm_client.default = {"event": "not-an-event"}
    try:
        container.conversation.decide(principal, "隨便問")
    except ExternalServiceError:
        pass
    else:
        raise AssertionError("invalid decision should raise")


# ---- LINE：事件 → 模組 ----
def test_free_text_goes_through_ai_and_shows_loading(post_line_event, container):
    _text(post_line_event, "哈囉")
    assert container.line_client.loading and container.llm_client.calls
    _, messages = container.line_client.replies[-1]
    assert isinstance(messages[0], TextMessage) and "你好" in messages[0].text
    labels = [i.action.label for i in messages[0].quick_reply.items]
    assert labels == ["🏥 找醫院", "⏰ 服藥提醒"]


def test_keyword_text_skips_ai(post_line_event, container):
    """完全等於 TEXT_KEYWORDS 的文字是確定進入點，直接走模組、不花模型呼叫。"""
    _text(post_line_event, "找醫院")
    assert container.llm_client.calls == []
    _, messages = container.line_client.replies[-1]
    assert isinstance(messages[0], TextMessage) and "分享位置" in messages[0].text


def test_emergency_keyword_short_circuits_without_ai(post_line_event, container):
    _text(post_line_event, "我胸痛")
    assert container.llm_client.calls == []
    _, messages = container.line_client.replies[-1]
    assert "119" in messages[0].text


def test_ai_emergency_event_uses_emergency_module(post_line_event, container):
    _text(post_line_event, "我覺得要叫 119")
    _, messages = container.line_client.replies[-1]
    assert messages[0].text == "請立即撥打 119。" and "119" in messages[1].text
    assert messages[1].quick_reply.items[0].action.label == "撥打 119"


def test_emergency_card_not_repeated_within_cooldown(post_line_event, container):
    """紅旗關鍵字用包含比對，接著描述「胸痛好一點了」不該每句都重傳緊急畫面：
    冷卻期間內改交給 AI 依脈絡判斷。"""
    from app.line.messages.common import EMERGENCY_TEXT

    _text(post_line_event, "我胸痛")
    assert container.llm_client.calls == []
    assert container.line_client.replies[-1][1][0].text == EMERGENCY_TEXT

    _text(post_line_event, "胸痛好一點了，頭痛怎麼辦")
    assert len(container.llm_client.calls) == 1  # 交給 AI，不再短路
    _, messages = container.line_client.replies[-1]
    assert messages[0].text != EMERGENCY_TEXT and "家醫科" in messages[0].text

    # AI 仍判斷 emergency：只回它的一句話 + 119 / 找醫院按鈕，不重貼固定文案
    _text(post_line_event, "我覺得要叫 119")
    _, messages = container.line_client.replies[-1]
    assert len(messages) == 1 and messages[0].text == "請立即撥打 119。"
    assert messages[0].quick_reply.items[0].action.label == "撥打 119"

    # 冷卻過期（或別的使用者）→ 再次短路
    container.conversation_state.clear("U1:emergency")
    _text(post_line_event, "我胸痛")
    assert container.line_client.replies[-1][1][0].text == EMERGENCY_TEXT
    _text(post_line_event, "我胸痛", user_id="U2")
    assert container.line_client.replies[-1][1][0].text == EMERGENCY_TEXT


def test_emergency_cooldown_keeps_other_states(post_line_event, container):
    """冷卻旗標用獨立 key，不會蓋掉等照片 / 等位置這類對話狀態。"""
    _text(post_line_event, "找醫院")
    assert container.conversation_state.peek("U1").action == "await_location"
    _text(post_line_event, "我覺得要叫 119")
    assert container.conversation_state.peek("U1:emergency") is not None
    assert container.conversation_state.peek("U1") is None  # 改打字聊別的，等位置狀態照舊丟掉


def test_hospital_event_asks_for_location_even_with_city(post_line_event, container):
    """找醫院只認使用者位置：AI 就算抓到縣市，也只會請對方分享位置。"""
    container.triage.poll()
    _text(post_line_event, "台北市哪家急診比較不塞")
    _, messages = container.line_client.replies[-1]
    texts = [m.text for m in messages if isinstance(m, TextMessage)]
    assert texts[0].startswith("幫你看台北市") and any("分享位置" in t for t in texts)
    assert not any(isinstance(m, FlexMessage) for m in messages)


def test_medication_event_with_drug_name_runs_lookup(post_line_event, container):
    container.pharmacies.import_drugs(
        [
            {
                "藥品代號": "A1",
                "藥品英文名稱": "METFORMIN HCL",
                "藥品中文名稱": "美佛明",
                "成分": "METFORMIN",
                "ATC代碼": "A10BA02",
                "有效迄日": "20991231",
            }
        ]
    )
    _text(post_line_event, "Metformin 是什麼藥")
    _, messages = container.line_client.replies[-1]
    assert "降血糖" in messages[0].text
    assert "美佛明" in str(
        messages[1].contents.to_dict() if isinstance(messages[1], FlexMessage) else messages[1].text
    )


def test_pharmacy_reminder_discharge_intake_events(post_line_event, container):
    _text(post_line_event, "附近有藥局嗎")
    _, messages = container.line_client.replies[-1]
    assert any(i.action.type == "location" for i in messages[0].quick_reply.items)
    _text(post_line_event, "我想設提醒")
    _, messages = container.line_client.replies[-1]
    assert "服藥小幫手" in messages[0].text
    _text(post_line_event, "出院後要注意什麼")
    _, messages = container.line_client.replies[-1]
    assert isinstance(messages[0], FlexMessage)
    _text(post_line_event, "怎麼填基本資料")
    _, messages = container.line_client.replies[-1]
    assert isinstance(messages[0], FlexMessage) and "intake" in str(messages[0].contents.to_dict())


def test_symptom_answer_with_actions_and_urgent_adds_119(post_line_event, container):
    # 病況諮詢：watch（建議就醫、非急症）→ 補「附近診所」讓病人就近看診
    _text(post_line_event, "我頭痛兩天")
    _, messages = container.line_client.replies[-1]
    assert "家醫科" in messages[0].text
    items = messages[0].quick_reply.items
    assert [i.action.label for i in items] == ["🏠 附近診所", "🏥 找醫院"]
    assert "action=clinics" in items[0].action.data
    assert "department=" in items[0].action.data  # 建議的科別帶進找診所
    _text(post_line_event, "我很喘")
    _, messages = container.line_client.replies[-1]
    labels = [i.action.label for i in messages[0].quick_reply.items]
    assert labels[:2] == ["🚨 撥打 119", "🏥 找醫院"]


def _verified_with_encounter(container, make_user, line_user_id="U1"):
    user = make_user(Role.PATIENT, BindingStatus.VERIFIED, line_user_id=line_user_id)
    if container.hospital_repo.get("H1") is None:
        container.hospital_repo.create(id="H1", name="第一醫院", region="台北市")
    record = container.patient_record_repo.create(user_id=user.id, hospital_id="H1")
    container.patient_record_repo.add_child(
        "encounters",
        record["id"],
        encounter_type="inpatient",
        visit_date="2026-09-20",
        department="心臟內科",
        diagnoses=[{"icd10_code": "I50.9", "description": "心臟衰竭"}],
        discharged_at="2026-09-25",
        discharge_code="HF-01",
    )
    return user


def test_discharge_context_includes_patient_data_for_verified_user(container, make_user):
    guest = container.users.to_principal(make_user(Role.GUEST, line_user_id="Ug"))
    assert container.conversation.build_context(guest, "出院後要注意什麼").patient_block is None

    user = _verified_with_encounter(container, make_user, line_user_id="Uv")
    principal = container.users.to_principal(user)
    context = container.conversation.build_context(principal, "出院後要注意什麼")
    assert context.patient_block and "心臟衰竭" in context.patient_block
    assert "心臟內科" in context.patient_block and "HF-01" in context.patient_block
    assert "【病人資料】" in container.conversation.build_prompt("x", context)


def test_discharge_event_personalized_answer_for_verified_user(
    post_line_event, container, make_user
):
    _verified_with_encounter(container, make_user)
    container.llm_client.decisions.insert(
        0,
        (
            "出院",
            {
                "event": "discharge",
                "answer": "心臟衰竭出院後今天要做的事：\n・每天早上量體重\n・限制鹽分\n・體重兩天增加 2 公斤請回診",
                "actions": ["reminder_menu"],
            },
        ),
    )
    # 自由文字 → AI 依病人資料寫出個人化衛教，直接回答（不再回通用版選單）
    _text(post_line_event, "出院後要注意什麼")
    _, messages = container.line_client.replies[-1]
    assert isinstance(messages[0], TextMessage) and "量體重" in messages[0].text
    assert [i.action.label for i in messages[0].quick_reply.items] == ["⏰ 服藥提醒"]
    # Rich Menu「我剛出院」→ DischargeHandler 也走 AI 個人化版
    post_line_event(
        _event("postback", user_id="U1", postback={"data": f"intent={Intent.DISCHARGE.value}"})
    )
    _, messages = container.line_client.replies[-1]
    assert "量體重" in messages[0].text
    # AI 失敗 → 退回通用版 Flex
    container.llm_client.error = ExternalServiceError("timeout")
    post_line_event(
        _event("postback", user_id="U1", postback={"data": f"intent={Intent.DISCHARGE.value}"})
    )
    _, messages = container.line_client.replies[-1]
    assert isinstance(messages[0], FlexMessage)


def test_unknown_asks_for_clarification(post_line_event, container):
    _text(post_line_event, "嗯")
    _, messages = container.line_client.replies[-1]
    assert "多說一點" in messages[0].text


def test_ai_failure_falls_back_to_keywords(post_line_event, container):
    container.llm_client.error = ExternalServiceError("down")
    _text(post_line_event, "找醫院")
    _, messages = container.line_client.replies[-1]
    assert "分享位置" in messages[0].text  # 關鍵字退路
    _text(post_line_event, "隨便聊聊")
    _, messages = container.line_client.replies[-1]
    assert "選單" in messages[0].text


def test_ai_disabled_falls_back(post_line_event, container):
    container.conversation.enabled = False
    _text(post_line_event, "服藥提醒")
    assert container.llm_client.calls == []
    _, messages = container.line_client.replies[-1]
    assert "服藥小幫手" in messages[0].text


def test_locked_user_is_blocked_before_ai(post_line_event, container):
    post_line_event(_event("follow", user_id="Ulock", follow={"isUnblocked": False}))
    user = container.user_repo.get_by_line_user_id("Ulock")
    container.user_repo.update(user.id, binding_status=BindingStatus.LOCKED)
    _text(post_line_event, "哈囉", user_id="Ulock")
    assert container.llm_client.calls == []
    _, messages = container.line_client.replies[-1]
    assert "鎖定" in messages[0].text


def test_text_clears_pending_photo_state_but_keeps_location(post_line_event, container):
    from app.line.intents import postback_data
    from app.line.state_store import AWAIT_PRESCRIPTION_PHOTO, PENDING_LOCATION

    post_line_event(_event("postback", postback={"data": postback_data(Intent.MEDICATION)}))
    assert container.conversation_state.peek("U1").action == AWAIT_PRESCRIPTION_PHOTO
    _text(post_line_event, "哈囉")
    assert container.conversation_state.peek("U1") is None
    container.conversation_state.set("U1", PENDING_LOCATION, {"address": "x"})
    _text(post_line_event, "哈囉")
    assert container.conversation_state.peek("U1").action == PENDING_LOCATION


# ---- 衛教 txt 匯入（依科別）與混合檢索 ----

_SEP = "=" * 60
_HYPERTENSION = (
    "高血壓居家照護\n\n首頁 > 衛教資訊 > 衛教單張\n\nQrcode\n\n"
    "高血壓病人要規律服藥並每天量血壓，血壓控制好可以減少中風與心肌梗塞的風險。\n\n"
    "一、每天量血壓\n\n早晚各量一次，量之前先靜坐五分鐘，記錄在血壓手冊帶回診時給醫師看。\n\n"
    "二、飲食與生活\n\n少鹽少油，每週運動三次、每次三十分鐘，戒菸並避免熬夜。\n\n"
    "三、什麼情況要就醫\n\n有下列情況請儘快就醫：\n\n1. 血壓超過 180/110 且頭痛。\n\n2. 胸痛或呼吸困難。"
)
_CATHETER = (
    "心導管檢查是從手腕或鼠蹊部的血管放入細管到心臟，檢查冠狀動脈有沒有狹窄，必要時同時放支架。\n\n"
    "檢查前一晚午夜後禁食，慢性病藥物依醫師指示服用，抗凝血劑要先告知醫師。\n\n"
    "Q：檢查後多久可以下床？\n\n手腕穿刺約四小時、鼠蹊部穿刺約八到十二小時，依護理師指示活動。\n\n"
    "檢查後穿刺處若出血、腫脹或手腳冰冷發麻，請立即告知醫護人員。"
)
_DIABETIC_FOOT = (
    "糖尿病病人每天要檢查足部有沒有傷口、水泡或顏色改變。\n\n"
    "洗腳後要擦乾趾縫，剪指甲要平剪不要剪太短，不要赤腳走路。\n\n"
    "若傷口三天沒有好轉或出現紅腫熱痛，請儘快回診新陳代謝科。"
)


def _record(title, hospital, level, url, text, category="衛教資訊 > 衛教單張"):
    return f"{_SEP}\n標題：{title}\n醫院：{hospital}（{level}）\n來源分類：{category}\n網址：{url}\n\n{text}\n\n"


DEPARTMENT_FILES = {
    "01_心臟內科.txt": "【心臟內科】共 5 篇衛教\n來源：台灣醫院官網衛教專區\n\n"
    + _record(
        "高血壓居家照護", "台大醫院", "醫學中心", "https://www.ntuh.gov.tw/edu/1", _HYPERTENSION
    )
    + _record(
        "心導管檢查須知",
        "中國醫藥大學附設醫院",
        "醫學中心",
        "https://www.cmuh.cmu.edu.tw/HealthEdus/Detail?no=2",
        _CATHETER,
    )
    # 與第一篇內文相同（他院轉載）→ 重複略過
    + _record(
        "高血壓居家照護（轉載）", "某某醫院", "地區醫院", "https://example.org/dup", _HYPERTENSION
    )
    # 太短 → 略過
    + _record("醫師停診公告", "博仁醫院", "地區醫院", "https://example.org/short", "本週三停診。")
    # 英文頁面 → 略過
    + _record(
        "Contact Information",
        "國軍醫院",
        "區域醫院",
        "https://example.org/en",
        "Extra! Extra! September is Military Personnel Month. " * 5,
    ),
    "57_未分類.txt": "【未分類】共 1 篇衛教\n\n"
    + _record(
        "糖尿病足部照護", "台大醫院", "醫學中心", "https://www.ntuh.gov.tw/edu/2", _DIABETIC_FOOT
    ),
    # 非衛教整檔略過
    "56_非衛教（院務消息、活動、醫師介紹）.txt": "【非衛教】共 1 篇\n\n"
    + _record(
        "本院榮獲醫療品質獎", "台大醫院", "醫學中心", "https://example.org/news", "本院榮獲…" * 40
    ),
}


def _write_department_files(tmp_path, files=DEPARTMENT_FILES):
    paths = []
    for name, content in files.items():
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        paths.append(path)
    return paths


def _import_departments(container, actor, tmp_path, **kwargs):
    return container.knowledge.import_department_files(
        actor, _write_department_files(tmp_path), min_chars=60, **kwargs
    )


def test_department_import_filters_dedupes_and_updates(container, make_user, tmp_path):
    actor = _system_actor(container, make_user)
    result = _import_departments(container, actor, tmp_path)
    assert {
        k: result[k] for k in ("files", "read", "created", "updated", "skipped", "duplicates")
    } == {
        "files": 2,
        "read": 6,
        "created": 3,
        "updated": 0,
        "skipped": 2,
        "duplicates": 1,
    }
    assert result["chunks"]["count"] >= 3 and result["chunks"]["over_hard_max"] == 0
    docs = {d["title"]: d for d in container.knowledge.list(actor)}
    doc = docs["高血壓居家照護"]
    assert doc["category"] == "education" and doc["hospital_id"] is None
    assert doc["department"] == "心臟內科" and doc["tags"] == ["心臟內科"]
    assert doc["meta"]["hospital_name"] == "台大醫院" and doc["meta"]["level"] == "醫學中心"
    assert doc["meta"]["imported_from"] == "01_心臟內科.txt"
    assert doc["source"] == "https://www.ntuh.gov.tw/edu/1"
    foot = docs["糖尿病足部照護"]
    assert foot["department"] is None and foot["tags"] == []
    assert container.knowledge.stats()["documents"] == 3

    # 片段：麵包屑 / Qrcode 清掉、標題路徑前綴、條列成組
    chunks = container.knowledge_repo.pending_embeddings(100)
    contents = [c["content"] for c in chunks]
    assert not any("Qrcode" in c or "首頁" in c for c in contents)
    hits = container.knowledge.retrieve("血壓要怎麼量")
    assert hits[0]["title"] == "高血壓居家照護" and hits[0]["department"] == "心臟內科"
    # 短小節併成一段時，小節名變成行內前綴；條列跟著引言
    assert "每天量血壓：早晚各量一次" in hits[0]["content"]
    assert "有下列情況請儘快就醫：\n1. 血壓超過 180/110" in hits[0]["content"]

    # 重跑：同網址更新、不重複建檔
    again = _import_departments(container, actor, tmp_path)
    assert again["created"] == 0 and again["updated"] == 3 and again["duplicates"] == 1
    assert container.knowledge.stats()["documents"] == 3
    # 非系統管理員不能匯入；檔名格式錯誤擋下
    from app.core.exceptions import AuthorizationError, ValidationError

    staff = _system_actor(container, make_user, role=Role.MEDICAL_STAFF, hospital_id="H1")
    with pytest.raises(AuthorizationError):
        _import_departments(container, staff, tmp_path)
    bad = tmp_path / "notes.txt"
    bad.write_text("x", encoding="utf-8")
    with pytest.raises(ValidationError):
        container.knowledge.import_department_files(actor, [bad])


def test_retrieve_weights_department_without_filtering(container, make_user, tmp_path):
    actor = _system_actor(container, make_user)
    wound = "傷口換藥前先洗手，用生理食鹽水清潔，再蓋上無菌紗布，每天換一次。"
    files = {
        "23_骨科.txt": _record(
            "骨折術後傷口照護",
            "A 醫院",
            "區域醫院",
            "https://a.example/1",
            wound + "石膏不要弄濕。" * 3,
        ),
        "36_皮膚科.txt": _record(
            "皮膚傷口照護", "B 醫院", "區域醫院", "https://b.example/1", wound + "避免抓癢。" * 3
        ),
    }
    container.knowledge.import_department_files(
        actor, _write_department_files(tmp_path, files), min_chars=30
    )
    assert (
        container.knowledge.retrieve("傷口怎麼換藥", department="骨科")[0]["department"] == "骨科"
    )
    assert (
        container.knowledge.retrieve("傷口怎麼換藥", department="皮膚科")[0]["department"]
        == "皮膚科"
    )
    # 加權不是過濾：其他科別的片段仍在結果內
    hits = container.knowledge.retrieve("傷口怎麼換藥", department="骨科")
    assert {h["department"] for h in hits} == {"骨科", "皮膚科"}


def test_department_import_and_rechunk_jobs(client, container, make_user, auth_header, tmp_path):
    superadmin = make_user(Role.SUPER_ADMIN)
    h1_admin = make_user(Role.HOSPITAL_ADMIN, hospital_id="H1")
    container.knowledge_jobs.spawn = lambda fn: fn()  # 同步執行

    def upload(user):
        return client.post(
            "/api/v1/admin/knowledge/jobs/department-import",
            data={
                "files": [
                    (io.BytesIO(DEPARTMENT_FILES["01_心臟內科.txt"].encode()), "01_心臟內科.txt"),
                    (io.BytesIO(DEPARTMENT_FILES["57_未分類.txt"].encode()), "57_未分類.txt"),
                ],
                "min_chars": "60",
            },
            headers=auth_header(user),
        )

    assert upload(h1_admin).status_code == 403
    resp = upload(superadmin)
    assert resp.status_code == 202
    job = resp.get_json()["data"]
    assert job["kind"] == "department_txt" and "path" not in job["params"]
    done = container.knowledge_jobs.get(container.users.to_principal(superadmin), job["id"])
    assert done["status"] == "succeeded" and done["result"]["created"] == 3
    assert not (container.knowledge_jobs._upload_dir / job["id"]).exists()  # 暫存目錄已刪

    # 先算向量，重建片段後向量清空、片段數不變
    container.knowledge.embed_pending()
    before = container.knowledge.stats()
    assert before["embedded"] == before["chunks"] > 0
    resp = client.post("/api/v1/admin/knowledge/jobs/rechunk", headers=auth_header(superadmin))
    assert resp.status_code == 202
    result = container.knowledge_jobs.get(
        container.users.to_principal(superadmin), resp.get_json()["data"]["id"]
    )["result"]
    after = container.knowledge.stats()
    assert result == {"documents": 3, "chunks": before["chunks"]}
    assert after["chunks"] == before["chunks"] and after["embedded"] == 0
    assert (
        client.post(
            "/api/v1/admin/knowledge/jobs/rechunk", headers=auth_header(h1_admin)
        ).status_code
        == 403
    )


def test_hybrid_retrieval_uses_embeddings_and_keywords(container, make_user, tmp_path):
    actor = _system_actor(container, make_user)
    _import_departments(container, actor, tmp_path)
    assert container.knowledge.stats()["embedded"] == 0
    done = container.knowledge.embed_pending(batch=1)
    assert done == container.knowledge.stats()["chunks"] > 0
    assert container.knowledge.stats()["embedded"] == done
    assert container.knowledge.embed_pending() == 0  # 已經算過的不會重算

    hits = container.knowledge.retrieve("糖尿病腳有傷口怎麼照顧")
    assert hits and hits[0]["title"] == "糖尿病足部照護"
    assert hits[0]["meta"]["hospital_name"] == "台大醫院" and hits[0]["source"].startswith(
        "https://"
    )
    assert len(container.llm_client.embed_calls[-1]) == 1  # 查詢也算了一次向量

    # 向量輸入帶科別與標題
    assert any(
        call[0].startswith("心臟內科 › 高血壓居家照護\n")
        for call in container.llm_client.embed_calls
    )
    # 沒有向量模型 → 只用關鍵字，結果仍然正確
    container.llm_client.embedding_model = None
    hits = container.knowledge.retrieve("心導管 檢查後 下床")
    assert hits[0]["title"] == "心導管檢查須知"
    container.llm_client.embedding_model = "fake-embedding"


def test_answer_with_citations_renders_sources(post_line_event, container, make_user, tmp_path):
    actor = _system_actor(container, make_user)
    _import_departments(container, actor, tmp_path)
    container.llm_client.decisions.insert(
        0,
        (
            "足部",
            {
                "event": "faq",
                "answer": "糖尿病足部照護重點：\n・每天檢查足部有無傷口\n・洗腳後擦乾趾縫\n・傷口三天沒好轉請回診",
                "citations": ["K1"],
            },
        ),
    )
    _text(post_line_event, "糖尿病足部要怎麼照顧")
    _, messages = container.line_client.replies[-1]
    assert isinstance(messages[0], FlexMessage)
    bubble = messages[0].contents.to_dict()
    assert "每天檢查足部" in bubble["body"]["contents"][0]["text"]
    buttons = [c for c in bubble["footer"]["contents"] if c["type"] == "button"]
    assert buttons[0]["action"]["uri"] == "https://www.ntuh.gov.tw/edu/2"
    assert "台大醫院" in buttons[0]["action"]["label"]

    # 沒有引用 → 純文字
    container.llm_client.decisions.insert(0, ("哈囉", {"event": "smalltalk", "answer": "你好"}))
    _text(post_line_event, "哈囉")
    _, messages = container.line_client.replies[-1]
    assert isinstance(messages[0], TextMessage)


def test_symptom_find_hospital_button_carries_department(post_line_event, container):
    """「找醫院」按鈕也帶科別，位置分享回來後優先推薦有該科的急救責任醫院。"""
    _text(post_line_event, "我頭痛兩天")
    _, messages = container.line_client.replies[-1]
    hospital = next(i for i in messages[0].quick_reply.items if i.action.label == "🏥 找醫院")
    assert "department=" in hospital.action.data
    post_line_event(_event("postback", postback={"data": hospital.action.data}))
    _, messages = container.line_client.replies[-1]
    assert "家醫科" in messages[0].text  # 入口文案說明會依症狀科別推薦
    state = container.conversation_state.peek("U1")
    assert state.payload == {"for": "hospital", "department": "家醫科"}


def test_discharge_department_choice_answers_without_verification(post_line_event, container):
    """通用版出院衛教：選了科別後模型有答案就直接回，不再退回「選擇科別」卡片而一直繞圈。"""
    post_line_event(_event("postback", postback={"data": f"intent={Intent.DISCHARGE.value}"}))
    _, messages = container.line_client.replies[-1]
    assert isinstance(messages[0], FlexMessage)
    post_line_event(_event("postback", postback={"data": "intent=discharge&step=department"}))
    _, messages = container.line_client.replies[-1]
    items = messages[0].quick_reply.items
    assert items[0].action.label == "心臟內科" and "從心臟內科出院" in items[0].action.text
    container.llm_client.decisions.insert(
        0,
        (
            "從心臟內科出院",
            {
                "event": "discharge",
                "answer": "心臟內科出院後：\n・每天量體重\n・限制鹽分",
                "citations": ["K1"],
            },
        ),
    )
    _text(post_line_event, items[0].action.text)
    _, messages = container.line_client.replies[-1]
    assert isinstance(messages[0], TextMessage) and "量體重" in messages[0].text
    # 模型沒答案（知識庫沒該科資料）→ 回說明文字，不是再一張選科別卡片
    container.llm_client.decisions.insert(
        0, ("從其他科別出院", {"event": "discharge", "answer": ""})
    )
    _text(post_line_event, "我剛從其他科別出院，回家要注意什麼、怎麼照護？")
    _, messages = container.line_client.replies[-1]
    assert isinstance(messages[0], TextMessage) and "知識庫目前沒有" in messages[0].text


def test_department_in_text_weights_retrieval(container, make_user):
    guest = container.users.to_principal(make_user(Role.GUEST, line_user_id="Ug"))
    calls: list[dict] = []
    original = container.knowledge.retrieve
    container.knowledge.retrieve = lambda *a, **kw: calls.append(kw) or original(*a, **kw)
    container.conversation.build_context(guest, "我剛從腸胃科出院，回家要注意什麼")
    assert calls[-1]["department"] == "肝膽腸胃內科"


def test_history_is_trimmed_and_cleared_on_keyword(post_line_event, container):
    _text(post_line_event, "我頭痛兩天")
    principal = container.users.to_principal(container.user_repo.get_by_line_user_id("U1"))
    history = container.conversation._history(principal)
    assert [t["role"] for t in history] == ["user", "assistant"]
    assert len(history[1]["text"]) <= 120
    assert "不要重複其中內容" in container.conversation.build_prompt(
        "x", container.conversation.build_context(principal, "x")
    )
    _text(post_line_event, "主選單")  # Rich Menu 關鍵字 = 換話題，清掉對話紀錄
    assert container.conversation._history(principal) == []
