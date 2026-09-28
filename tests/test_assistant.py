"""AI 對話助理：檢索（jieba + BM25）、知識庫 CRUD 與院所隔離、脈絡組裝、事件 → 模組對應、退路。"""

from __future__ import annotations

import io
import json
from pathlib import Path

from linebot.v3.messaging import FlexMessage, TextMessage

from app.core.exceptions import ExternalServiceError
from app.domain.enums import BindingStatus, Intent, Role
from app.domain.retrieval import BM25Index, chunk_text, tokenize
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


# ---- 衛教 JSONL 匯入與混合檢索 ----

HEALTH_ED_RECORDS = [
    {
        "id": "a1",
        "title": "了解肝癌",
        "text": "肝癌是台灣常見的癌症之一。\n\n高風險族群包括 B 型肝炎、C 型肝炎帶原者與肝硬化病人，建議每六個月做一次腹部超音波與甲型胎兒蛋白檢查。\n\n預防方式：接種 B 型肝炎疫苗、避免飲酒、控制體重。",
        "chars": 120,
        "hospitalName": "中國醫藥大學附設醫院",
        "hospitalCode": "0401200011",
        "level": "醫學中心",
        "topics": ["癌症/腫瘤", "腸胃/肝膽"],
        "forms": ["疾病介紹"],
        "category": "衛教單張",
        "format": "html",
        "url": "https://www.cmuh.cmu.edu.tw/HealthEdus/Detail?no=1",
    },
    {
        "id": "a2",
        "title": "糖尿病足部照護",
        "text": "糖尿病病人每天要檢查足部有沒有傷口、水泡或顏色改變。\n\n洗腳後要擦乾趾縫，剪指甲要平剪不要剪太短，不要赤腳走路。\n\n若傷口三天沒有好轉或出現紅腫熱痛，請儘快回診新陳代謝科。",
        "chars": 110,
        "hospitalName": "台大醫院",
        "hospitalCode": "0401180014",
        "level": "醫學中心",
        "topics": ["內分泌/新陳代謝"],
        "forms": ["居家照護"],
        "category": "衛教資訊",
        "format": "pdf",
        "url": "https://www.ntuh.gov.tw/edu/2",
    },
    {  # 與 a2 內文相同（他院轉載）→ 重複略過
        "id": "a3",
        "title": "糖尿病足部照護（轉載）",
        "text": "糖尿病病人每天要檢查足部有沒有傷口、水泡或顏色改變。\n\n洗腳後要擦乾趾縫，剪指甲要平剪不要剪太短，不要赤腳走路。\n\n若傷口三天沒有好轉或出現紅腫熱痛，請儘快回診新陳代謝科。",
        "hospitalName": "某某醫院",
        "hospitalCode": "9999",
        "level": "地區醫院",
        "topics": ["內分泌/新陳代謝"],
        "forms": [],
        "url": "https://example.org/dup",
    },
    {  # 英文頁面 → 略過
        "id": "a4",
        "title": "Contact Information",
        "text": "Extra! Extra! September is Military Personnel Month. " * 5,
        "hospitalName": "國軍醫院",
        "hospitalCode": "0502",
        "topics": [],
        "forms": [],
        "url": "https://example.org/en",
    },
    {  # 太短 → 略過
        "id": "a5",
        "title": "醫師停診公告",
        "text": "本週三停診。",
        "hospitalName": "博仁醫院",
        "hospitalCode": "1501",
        "topics": ["其他"],
        "forms": [],
        "url": "https://example.org/short",
    },
    {  # 沒有主題分類 → 預設略過，--all 才匯入
        "id": "a6",
        "title": "院內停車資訊",
        "text": "本院停車場位於地下二樓，就醫民眾憑當日掛號單可享三小時免費停車，超過每小時三十元；假日車位有限，建議搭乘大眾運輸。"
        * 2,
        "hospitalName": "台大醫院",
        "hospitalCode": "0401180014",
        "topics": [],
        "forms": [],
        "url": "https://www.ntuh.gov.tw/parking",
    },
]


def _write_jsonl(tmp_path, records):
    path = tmp_path / "he.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")
    return path


def test_health_education_import_filters_and_dedupes(container, make_user, tmp_path):
    actor = _system_actor(container, make_user)
    path = _write_jsonl(tmp_path, HEALTH_ED_RECORDS)
    result = container.knowledge.import_health_education(actor, path, min_chars=60)
    assert result == {"created": 2, "skipped": 3, "duplicates": 1, "read": 6}
    docs = {d["title"]: d for d in container.knowledge.list(actor)}
    doc = docs["了解肝癌"]
    assert doc["category"] == "education" and doc["hospital_id"] is None
    assert doc["meta"]["hospital_name"] == "中國醫藥大學附設醫院"
    assert doc["meta"]["topics"] == ["癌症/腫瘤", "腸胃/肝膽"] and doc["tags"] == [
        "癌症/腫瘤",
        "腸胃/肝膽",
        "疾病介紹",
    ]
    assert doc["source"].startswith("https://www.cmuh.cmu.edu.tw")
    assert container.knowledge.stats()["documents"] == 2

    # 重跑：同網址略過，不會重複
    again = container.knowledge.import_health_education(actor, path, min_chars=60)
    assert again["created"] == 0 and container.knowledge.stats()["documents"] == 2
    # --all 把沒分類的也收進來；非系統管理員不能匯入
    result = container.knowledge.import_health_education(
        actor, path, min_chars=60, classified_only=False
    )
    assert result["created"] == 1
    from app.core.exceptions import AuthorizationError

    staff = _system_actor(container, make_user, role=Role.MEDICAL_STAFF, hospital_id="H1")
    try:
        container.knowledge.import_health_education(staff, path)
        raise AssertionError("expected AuthorizationError")
    except AuthorizationError:
        pass


def test_hybrid_retrieval_uses_embeddings_and_keywords(container, make_user, tmp_path):
    actor = _system_actor(container, make_user)
    container.knowledge.import_health_education(
        actor, _write_jsonl(tmp_path, HEALTH_ED_RECORDS), min_chars=60
    )
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

    # 沒有向量模型 → 只用關鍵字，結果仍然正確
    container.llm_client.embedding_model = None
    hits = container.knowledge.retrieve("肝癌 高風險 追蹤")
    assert hits[0]["title"] == "了解肝癌"
    container.llm_client.embedding_model = "fake-embedding"


def test_answer_with_citations_renders_sources(post_line_event, container, make_user, tmp_path):
    actor = _system_actor(container, make_user)
    container.knowledge.import_health_education(
        actor, _write_jsonl(tmp_path, HEALTH_ED_RECORDS), min_chars=60
    )
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
