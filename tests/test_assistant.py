"""AI 對話助理：檢索（jieba + BM25）、知識庫 CRUD 與院所隔離、脈絡組裝、事件 → 模組對應、退路。"""

from __future__ import annotations

from linebot.v3.messaging import FlexMessage, TextMessage

from app.core.exceptions import ExternalServiceError
from app.domain.enums import BindingStatus, Intent, Role
from app.domain.intake_schema import INTAKE_STEPS
from app.domain.retrieval import (
    BM25Index,
    chunk_document,
    chunk_text,
    tokenize,
)
from tests.test_webhook import _event


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


# 通用知識文件（測試自帶，不依賴本機未進版控的 data/ 目錄）
SEED_DOCUMENTS = (
    (
        "慢性病連續處方箋怎麼領",
        "medication",
        "慢性病連續處方箋（慢箋）一張可以領三次藥，每次領 28 到 30 天的份量。\n\n"
        "第二、三次領藥可以到健保特約藥局，不用回醫院掛號；上一次的藥吃完前十天內就可以領下一次。",
    ),
    (
        "服藥安全與漏吃藥",
        "medication",
        "漏吃一次藥時，想起來就盡快補吃；如果已經接近下一次吃藥的時間，就跳過這一次。\n\n"
        "不要一次吃兩倍的劑量，也不要自行停藥，有疑問請問藥師或原開立處方的醫師。",
    ),
    (
        "服務說明",
        "service",
        "MediAgent+ 可以幫你找附近的急救責任醫院與藥局、設定服藥提醒，並回答衛教問題。\n\n"
        "緊急狀況請直接撥打 119，不要等待機器人回覆。",
    ),
)


def _seed_knowledge(container, actor):
    """建立幾份通用知識文件。"""
    for title, category, content in SEED_DOCUMENTS:
        container.knowledge.create(
            actor, {"title": title, "category": category, "content": content}
        )


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


# ---- 知識庫 ----
def test_seed_documents_retrieve(container, make_user):
    actor = _system_actor(container, make_user)
    _seed_knowledge(container, actor)
    assert container.knowledge.stats()["documents"] == len(SEED_DOCUMENTS)
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
    # 知識庫以院所為單位：院方帳號只列自己院所的文件，通用與別家的看不到
    assert container.knowledge.list(h2_staff) == []
    assert {d["title"] for d in container.knowledge.list(h1_staff)} == {"第一醫院停車"}
    assert {d["title"] for d in container.knowledge.list(superadmin)} == {"第一醫院停車", "通用"}

    def titles(hospital_ids):
        hits = container.knowledge.retrieve("停車場", hospital_ids=hospital_ids)
        return {h["title"] for h in hits}

    # 明確指定範圍時只含列出的：None 元素 = 通用；不給範圍 = 不限院所（共用池）
    assert titles([None]) == {"通用"}
    assert titles([None, "H1"]) == {"通用", "第一醫院停車"}
    assert titles(["H1"]) == {"第一醫院停車"}
    assert titles([None, "H2"]) == {"通用"}
    assert titles(None) == {"通用", "第一醫院停車"}
    # 後台只管得到本院的文件，但檢索是共用池：別家院所的人也查得到
    preview = container.knowledge.search_preview(h2_staff, "停車場")
    assert {h["title"] for h in preview} == {"通用", "第一醫院停車"}


def test_retrieve_prefers_own_hospital_only_when_relevant(container, make_user):
    wound = "傷口換藥前先洗手，用生理食鹽水清潔，再蓋上無菌紗布，每天換一次。"
    for hospital_id, title in (("H1", "第一醫院傷口照護"), ("H2", "第二診所傷口照護")):
        actor = _system_actor(container, make_user, Role.HOSPITAL_ADMIN, hospital_id)
        container.knowledge.create(
            actor, {"title": title, "content": wound + title, "category": "education"}
        )

    def first(query, prefer):
        return container.knowledge.retrieve(query, prefer_hospital_ids=prefer)[0]["hospital_id"]

    # 兩家都有相關內容 → 病人自己院所的排第一；另一家的仍在結果內（不是過濾）
    assert first("傷口怎麼換藥", ["H1"]) == "H1" and first("傷口怎麼換藥", ["H2"]) == "H2"
    hits = container.knowledge.retrieve("傷口怎麼換藥", prefer_hospital_ids=["H1"])
    assert {h["hospital_id"] for h in hits} == {"H1", "H2"}
    # 本院沒有相關內容 → 用其他院所的，不會硬把本院不相干的文件排到前面
    h1_admin = _system_actor(container, make_user, Role.HOSPITAL_ADMIN, "H1")
    container.knowledge.create(
        h1_admin,
        {"title": "糖尿病足部照護", "content": _DIABETIC_FOOT, "category": "education"},
    )
    assert first("糖尿病足部要怎麼檢查", ["H2"]) == "H1"


def test_conversation_retrieval_prefers_patient_hospital(container, make_user):
    calls: list[dict] = []
    original = container.knowledge.retrieve
    container.knowledge.retrieve = lambda *a, **kw: calls.append(kw) or original(*a, **kw)
    # 衛教資訊是共用池：不論有沒有院所都查全部；沒有院所就沒有優先對象
    guest = make_user(Role.GUEST, line_user_id="Ug")
    container.conversation.build_context(container.users.to_principal(guest), "停車場在哪")
    assert "hospital_ids" not in calls[-1] and calls[-1]["prefer_hospital_ids"] == []
    # 有就醫院所的病人 → 一樣查全部，本院的優先
    container.patient_repo.save_step(guest.id, INTAKE_STEPS[0], {})
    container.patient_repo.set_hospital(guest.id, "H1", confirmed=True)
    container.conversation.build_context(container.users.to_principal(guest), "停車場在哪")
    assert "hospital_ids" not in calls[-1] and calls[-1]["prefer_hospital_ids"] == ["H1"]


def test_detect_department_prefers_hospital_own_names(container, make_user):
    h1_staff = _system_actor(container, make_user, Role.HOSPITAL_ADMIN, "H1")
    doc = container.knowledge.create(
        h1_staff,
        {"title": "心臟復健", "content": "術後 復健 運動 注意事項", "category": "education"},
    )
    container.knowledge_repo.update(doc["id"], department="心血管中心")
    detect = container.knowledge.detect_department
    # 各院所可以有自己的科別分類：範圍內用到的名稱優先，別家的不算；沒有就退回通用科別表 / 別名
    assert detect(["我剛從心血管中心出院"], [None, "H1"]) == "心血管中心"
    assert detect(["我剛從心血管中心出院"], [None, "H2"]) is None
    assert detect(["我剛從腸胃科出院"], [None, "H1"]) == "肝膽腸胃內科"
    assert detect(["回家要注意什麼", "心臟科"], [None, "H1"]) == "心臟內科"


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
    # 讀取：院所管理員只看得到自己院所的文件（通用的列表沒有、單篇 403），搜尋可用
    rows = client.get("/api/v1/admin/knowledge", headers=auth_header(h1_admin)).get_json()["data"]
    assert {r["title"] for r in rows} == {"本院"}
    assert (
        client.get(
            f"/api/v1/admin/knowledge/{generic_id}", headers=auth_header(h1_admin)
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/v1/admin/knowledge/search?q=FAQ", headers=auth_header(h1_admin)
        ).status_code
        == 200
    )
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
    _seed_knowledge(container, actor)
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


def test_answer_markdown_is_stripped(post_line_event, container):
    """LINE 不渲染 Markdown：模型仍輸出時，符號不會原樣出現在使用者畫面上。"""
    from app.domain.assistant_schema import plain_text

    raw = (
        "## 結論\n**多喝水**並休息。\n- 觀察體溫\n* 少量多餐\n"
        "> 持續不適請就醫\n詳見 [衛教單](https://example.org/a) 與 `退燒藥`。\n2 * 3 = 6"
    )
    assert plain_text(raw) == (
        "結論\n多喝水並休息。\n・觀察體溫\n・少量多餐\n持續不適請就醫\n詳見 衛教單 與 退燒藥。\n2 * 3 = 6"
    )
    container.llm_client.decisions.insert(0, ("喝水", {"event": "faq", "answer": "**多喝水**"}))
    _text(post_line_event, "要喝水嗎")
    _, messages = container.line_client.replies[-1]
    assert messages[0].text == "多喝水"


def _clinic(container, hospital_id, name, departments):
    container.hospital_repo.create(
        id=hospital_id,
        name=name,
        region="台北市",
        address=f"台北市大安區信義路三段{hospital_id[-1]}號",
        capacity_tier="clinic",
        departments=departments,
    )


def test_symptom_answer_with_actions_and_urgent_adds_119(post_line_event, container):
    # 病況諮詢：非急症 → 回答後直接請分享位置找診所（記下症狀的科別），不用再按「附近診所」
    _clinic(container, "C1", "大安家醫診所", "家庭醫學科")
    _clinic(container, "C2", "大安牙醫診所", "牙醫一般科")
    _text(post_line_event, "我頭痛兩天")
    _, messages = container.line_client.replies[-1]
    assert "家醫科" in messages[0].text and messages[0].quick_reply is None
    assert "有家醫科的診所" in messages[1].text
    items = messages[1].quick_reply.items
    assert items[0].action.type == "location"
    assert [i.action.label for i in items[1:]] == ["🏥 找醫院"]  # 模型給的其餘下一步接在後面
    state = container.conversation_state.peek("U1")
    assert state.action == "await_location"
    assert state.payload == {"for": "clinics", "department": "家醫科", "symptom": "頭痛兩天"}
    post_line_event(
        _event(
            "message",
            message={
                "type": "location",
                "id": "m2",
                "title": "我的位置",
                "address": "台北市大安區信義路三段10號",
                "latitude": 25.033,
                "longitude": 121.543,
            },
        )
    )
    _, messages = container.line_client.replies[-1]
    body = str(messages[0].contents.to_dict())
    assert "大安家醫診所" in body and "大安牙醫診所" not in body  # 依症狀的科別篩選
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


# ---- 衛教文件與混合檢索 ----

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


def _seed_education(container, actor):
    """三篇衛教（兩篇心臟內科、一篇沒有科別），出處醫院與原文網址跟著文件走。"""
    docs = (
        ("高血壓居家照護", "心臟內科", "台大醫院", "https://www.ntuh.gov.tw/edu/1", _HYPERTENSION),
        (
            "心導管檢查須知",
            "心臟內科",
            "中國醫藥大學附設醫院",
            "https://www.cmuh.cmu.edu.tw/HealthEdus/Detail?no=2",
            _CATHETER,
        ),
        ("糖尿病足部照護", None, "台大醫院", "https://www.ntuh.gov.tw/edu/2", _DIABETIC_FOOT),
    )
    for title, department, hospital, url, text in docs:
        doc = container.knowledge.create(
            actor,
            {
                "title": title,
                "content": text,
                "category": "education",
                "department": department,
                "source": url,
            },
        )
        container.knowledge_repo.update(doc["id"], meta={"hospital_name": hospital})


def test_education_documents_are_chunked_with_structure(container, make_user):
    actor = _system_actor(container, make_user)
    _seed_education(container, actor)
    docs = {d["title"]: d for d in container.knowledge.list(actor)}
    assert docs["高血壓居家照護"]["department"] == "心臟內科"
    assert docs["糖尿病足部照護"]["department"] is None
    assert all(d["chunk_count"] >= 1 for d in docs.values())  # 列表帶每篇的片段數

    hits = container.knowledge.retrieve("血壓要怎麼量")
    assert hits[0]["title"] == "高血壓居家照護" and hits[0]["department"] == "心臟內科"
    # 片段：麵包屑 / Qrcode 清掉；短小節併成一段時小節名變成行內前綴；條列跟著引言
    assert "Qrcode" not in hits[0]["content"] and "首頁" not in hits[0]["content"]
    assert "每天量血壓：早晚各量一次" in hits[0]["content"]
    assert "有下列情況請儘快就醫：\n1. 血壓超過 180/110" in hits[0]["content"]


def test_retrieve_weights_department_without_filtering(container, make_user):
    actor = _system_actor(container, make_user)
    wound = "傷口換藥前先洗手，用生理食鹽水清潔，再蓋上無菌紗布，每天換一次。"
    for title, department, extra in (
        ("骨折術後傷口照護", "骨科", "石膏不要弄濕。"),
        ("皮膚傷口照護", "皮膚科", "避免抓癢。"),
    ):
        container.knowledge.create(
            actor,
            {
                "title": title,
                "content": wound + extra * 3,
                "category": "education",
                "department": department,
            },
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


def test_hybrid_retrieval_uses_embeddings_and_keywords(container, make_user):
    actor = _system_actor(container, make_user)
    _seed_education(container, actor)
    # 後台新增文件後就順手算好向量；已經算過的不會重算
    stats = container.knowledge.stats()
    assert stats["embedded"] == stats["chunks"] > 0
    assert container.knowledge.embed_pending() == 0

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


def test_answer_with_citations_renders_sources(post_line_event, container, make_user):
    actor = _system_actor(container, make_user)
    _seed_education(container, actor)
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


def test_symptom_with_city_recommends_clinics_in_same_reply(post_line_event, container):
    """使用者說了所在縣市：回答之後同一次回覆就列出有該科的診所，不必分享位置。"""
    _clinic(container, "C1", "大安內科診所", "內科")
    _clinic(container, "C2", "大安牙醫診所", "牙醫一般科")
    container.llm_client.decisions.insert(
        0,
        (
            "肚子痛",
            {
                "event": "symptom",
                "risk": "none",
                "slots": {"department": "內科", "city": "台北市", "district": "大安區"},
                "answer": "肚子痛可先觀察，持續不適建議看內科。",
            },
        ),
    )
    _text(post_line_event, "我在台北市大安區，肚子痛")
    _, messages = container.line_client.replies[-1]
    assert "內科" in messages[0].text
    assert isinstance(messages[1], FlexMessage)
    body = str(messages[1].contents.to_dict())
    assert "大安內科診所" in body and "大安牙醫診所" not in body
    assert container.conversation_state.peek("U1") is None  # 已經推薦完，不再等位置


def test_symptom_clinics_are_picked_by_model(post_line_event, container):
    """院所主檔沒有科別：同區院所的名稱交給模型挑，只列模型挑中的（醫美診所不會出現）。"""
    for hospital_id, name in (("C1", "A 美型診所"), ("C2", "大安內科診所"), ("C3", "大安診所")):
        _clinic(container, hospital_id, name, None)
    llm = container.llm_client
    llm.decisions.insert(
        0,
        (
            "肚子痛",
            {
                "event": "symptom",
                "slots": {"symptom": "肚子痛", "department": "內科"},
                "answer": "肚子痛可先觀察，持續不適建議看內科。",
            },
        ),
    )
    llm.clinic_picks = [
        2,
        99,
        2,
    ]  # 候選依名稱排序：1=A 美型、2=大安內科、3=大安；超出範圍 / 重複的丟掉
    _text(post_line_event, "我肚子痛")
    post_line_event(
        _event(
            "message",
            message={
                "type": "location",
                "id": "m2",
                "title": "我的位置",
                "address": "台北市大安區信義路三段10號",
                "latitude": 25.033,
                "longitude": 121.543,
            },
        )
    )
    pick_call = llm.calls[-1]
    assert pick_call["model"].__name__ == "ClinicPicks"
    assert "【症狀】肚子痛" in pick_call["prompt"] and "1｜A 美型診所｜診所" in pick_call["prompt"]
    _, messages = container.line_client.replies[-1]
    body = str(messages[0].contents.to_dict())
    assert "大安內科診所" in body and "美型" not in body
    # 模型一家都沒挑 → 明說找不到，不列不相干的診所
    llm.clinic_picks = []
    _text(post_line_event, "我肚子痛")
    post_line_event(
        _event(
            "message",
            message={
                "type": "location",
                "id": "m3",
                "title": "我的位置",
                "address": "台北市大安區信義路三段10號",
                "latitude": 25.033,
                "longitude": 121.543,
            },
        )
    )
    _, messages = container.line_client.replies[-1]
    assert len(messages) == 1 and "找不到適合看內科的診所" in messages[0].text


def test_clinic_event_finds_clinics_with_symptom_from_context(post_line_event, container):
    """「先看診所」→ clinic 事件：走找診所（不是急診醫院），症狀 / 科別由模型從對話脈絡帶上。"""
    container.llm_client.decisions.insert(
        0,
        (
            "先看診所",
            {"event": "clinic", "slots": {"symptom": "肚子痛", "department": "家庭醫學科"}},
        ),
    )
    _text(post_line_event, "先看診所")
    _, messages = container.line_client.replies[-1]
    assert "有家庭醫學科的診所" in messages[0].text
    assert messages[0].quick_reply.items[0].action.type == "location"
    state = container.conversation_state.peek("U1")
    assert state.payload == {"for": "clinics", "department": "家庭醫學科", "symptom": "肚子痛"}


def test_symptom_with_department_recommends_even_if_flagged_unclear(post_line_event, container):
    """模型給了完整建議與科別卻又標「需要再問清楚」：有科別就照樣接著找診所。"""
    container.llm_client.decisions.insert(
        0,
        (
            "肚子痛",
            {
                "event": "symptom",
                "slots": {"symptom": "肚子痛", "department": "內科"},
                "answer": "肚子痛建議先到內科或家庭醫學科看診。",
                "needs_clarification": True,
            },
        ),
    )
    _text(post_line_event, "我肚子痛")
    _, messages = container.line_client.replies[-1]
    assert len(messages) == 2 and "有內科的診所" in messages[1].text


def test_symptom_clarification_does_not_ask_location(post_line_event, container):
    """症狀太模糊、模型要再問一句時先不推薦診所。"""
    container.llm_client.decisions.insert(
        0,
        (
            "怪怪的",
            {
                "event": "symptom",
                "answer": "可以多說一點嗎？哪裡不舒服、多久了？",
                "needs_clarification": True,
            },
        ),
    )
    _text(post_line_event, "我覺得怪怪的")
    _, messages = container.line_client.replies[-1]
    assert len(messages) == 1
    assert container.conversation_state.peek("U1") is None


def test_symptom_find_hospital_button_carries_department(post_line_event, container):
    """「找醫院」按鈕也帶科別，位置分享回來後優先推薦有該科的急救責任醫院。"""
    _text(post_line_event, "我頭痛兩天")
    _, messages = container.line_client.replies[-1]
    hospital = next(i for i in messages[-1].quick_reply.items if i.action.label == "🏥 找醫院")
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
