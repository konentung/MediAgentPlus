from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

from app import create_app
from app.domain.enums import BindingStatus, Role
from app.domain.principal import Principal
from app.extensions import db
from app.integrations.nhi_er import ErFeed, parse_feed
from app.integrations.vision import ExtractedItem, PrescriptionExtraction
from app.repositories.user_repository import UserRecord

# 1x1 PNG，供圖片下載 / 處方辨識流程使用
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class FakeLineClient:
    """記錄所有 reply / push，不真的呼叫 LINE。"""

    def __init__(self) -> None:
        self.replies: list[tuple[str, list]] = []
        self.pushes: list[tuple[str, list]] = []
        self.loading: list[tuple[str, int]] = []
        self.downloaded: list[str] = []
        self.fail_push = False

    def reply(self, reply_token, messages):
        self.replies.append((reply_token, list(messages)))

    def push(self, to, messages):
        if self.fail_push:
            raise RuntimeError("LINE push failed")
        self.pushes.append((to, list(messages)))

    def show_loading_animation(self, chat_id, seconds=20):
        self.loading.append((chat_id, seconds))

    def get_message_content(self, message_id):
        self.downloaded.append(message_id)
        return TINY_PNG


def sample_extraction(**overrides) -> PrescriptionExtraction:
    base = dict(
        is_prescription=True,
        hospital_name="第一醫院",
        issued_on="2026-09-20",
        days_supply=28,
        is_chronic_refill=True,
        refill_seq=1,
        refill_total=3,
        items=[
            ExtractedItem(
                drug_name_raw="Metformin 500mg 膜衣錠",
                drug_name_en="Metformin",
                dose_text="1 tab",
                frequency_code="BID",
                timing="PC",
                duration_days=28,
                confidence=0.95,
            ),
            ExtractedItem(
                drug_name_raw="Zolpidem 10mg", frequency_code="HS", duration_days=14, confidence=0.6
            ),
        ],
        warnings=["第二項劑量字跡模糊"],
    )
    base.update(overrides)
    return PrescriptionExtraction(**base)


class FakeVisionClient:
    """回固定的辨識結果；`result` 可在測試中替換，`error` 設定後改為丟出該例外。"""

    model = "fake"

    def __init__(self) -> None:
        self.result = sample_extraction()
        self.error: Exception | None = None
        self.calls: list[tuple[int, str]] = []

    def extract_prescription(self, image, content_type):
        self.calls.append((len(image), content_type))
        if self.error:
            raise self.error
        return self.result


def er_row(hospital_id: str, name: str, area: str, *, hosp_type="2", **counts) -> dict:
    """健保署急診即時 API 的一列（欄位名稱是官方原樣）。"""
    return {
        "hosP_ID": hospital_id,
        "hosP_NAME": name,
        "areA_NO_N": area,
        "conT_TYPE": hosp_type,
        "inform": "Y" if counts.get("full_119") else "N",
        "waiT_SEE_CNT": str(counts.get("wait_see", 0)),
        "waiT_BED_CNT": str(counts.get("wait_bed", 0)),
        "waiT_GENERAL_CNT": str(counts.get("wait_admit", 0)),
        "waiT_ICU_CNT": str(counts.get("wait_icu", 0)),
        "url": None,
        "txT_DATE": counts.get("txt_date", "2026-09-26T14:45:03"),
    }


def sample_er_payload(txt_date: str | None = None) -> dict:
    """台北市：臺大壅塞（119 滿床）、仁愛有餘裕；新北市：亞東有餘裕；台中市：中國附醫偏忙。

    回報時間預設為「現在」（台灣時間），避免測試因實際時間流逝而被判定資料過舊。
    """
    if txt_date is None:
        from app.utils.clock import now_tw

        txt_date = now_tw().strftime("%Y-%m-%dT%H:%M:%S")
    return {
        "sysdate": txt_date.replace("T", " ")[:16],
        "data": [
            er_row(
                "0401180014",
                "臺大",
                "01",
                hosp_type="1",
                wait_see=5,
                wait_admit=80,
                wait_icu=6,
                full_119=True,
                txt_date=txt_date,
            ),
            er_row(
                "0101020017",
                "聯醫仁愛",
                "01",
                hosp_type="2",
                wait_see=1,
                wait_admit=1,
                txt_date=txt_date,
            ),
            er_row(
                "1131010011",
                "亞東",
                "31",
                hosp_type="1",
                wait_see=3,
                wait_admit=4,
                txt_date=txt_date,
            ),
            er_row(
                "1317050017",
                "中國附醫",
                "03",
                hosp_type="1",
                wait_see=25,
                wait_admit=35,
                wait_icu=1,
                txt_date=txt_date,
            ),
        ],
    }


class FakeErClient:
    """回固定的急診即時資料；測試可改 `payload` 或設 `error`。"""

    def __init__(self) -> None:
        self.payload = sample_er_payload()
        self.error: Exception | None = None
        self.calls = 0

    def fetch(self) -> ErFeed:
        self.calls += 1
        if self.error:
            raise self.error
        return parse_feed(self.payload)


class FakeLlmClient:
    """依關鍵字回固定決策；`decisions` 可在測試中改，`error` 設定後改為丟出該例外。

    模型只回結構化決策（event / answer / slots / actions），訊息長相由 AssistantHandler 套用模組決定。
    回傳值會經 `response_model.model_validate()`，不合法的決策會像真實後端一樣丟 ExternalServiceError。
    """

    model = "fake"
    embedding_model = "fake-embedding"  # 設為 None 可測「只用關鍵字檢索」

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.embed_calls: list[list[str]] = []
        self.error: Exception | None = None
        self.decisions: list[tuple[str, dict]] = [
            ("119", {"event": "emergency", "risk": "emergency", "answer": "請立即撥打 119。"}),
            (
                "台北市",
                {
                    "event": "hospital",
                    "slots": {"city": "台北市"},
                    "answer": "幫你看台北市的急診狀況。",
                },
            ),
            ("醫院", {"event": "hospital", "answer": ""}),
            (
                "metformin",
                {
                    "event": "medication",
                    "slots": {"drug_name": "Metformin"},
                    "answer": "Metformin 是常見的降血糖藥。",
                    "citations": ["K1"],
                },
            ),
            ("藥局", {"event": "pharmacy", "answer": ""}),
            ("提醒", {"event": "reminder", "answer": ""}),
            ("出院", {"event": "discharge", "answer": ""}),
            (
                "客服",
                {"event": "feedback", "answer": "目前沒有真人客服，可以用意見回饋表單反映。"},
            ),
            ("基本資料", {"event": "intake", "answer": "填寫基本資料後可使用個人化功能。"}),
            (
                "頭痛",
                {
                    "event": "symptom",
                    "risk": "watch",
                    "slots": {"symptom": "頭痛兩天", "department": "家醫科"},
                    "answer": "頭痛兩天可先掛家醫科或神經內科；若突然劇痛請撥 119。",
                    "actions": ["find_hospital"],
                    "citations": ["K1"],
                },
            ),
            (
                "喘",
                {
                    "event": "symptom",
                    "risk": "urgent",
                    "answer": "呼吸急促建議儘快就醫。",
                    "actions": ["find_hospital"],
                },
            ),
            (
                "哈囉",
                {
                    "event": "smalltalk",
                    "answer": "你好！有什麼可以幫你？",
                    "actions": ["find_hospital", "reminder_menu"],
                },
            ),
        ]
        self.default = {"event": "unknown", "answer": "", "needs_clarification": True}

    def embed(self, texts):
        """決定性的假向量：以斷詞結果做 bag-of-words，同主題的片段會比較相近。"""
        from app.domain.retrieval import tokenize

        self.embed_calls.append(list(texts))
        vectors = []
        for text in texts:
            vector = [0.0] * 32
            for token in tokenize(text):
                vector[hash(token) % 32] += 1.0
            vectors.append(vector)
        return vectors

    def complete_structured(self, prompt, response_model, *, system="", images=()):
        from app.integrations.llm import validate_output as _validate

        self.calls.append({"prompt": prompt, "model": response_model, "system": system})
        if self.error:
            raise self.error
        message = prompt.rsplit("【使用者這則訊息】", 1)[-1].lower()
        for keyword, decision in self.decisions:
            if keyword.lower() in message:
                return _validate(response_model, dict(decision))
        return _validate(response_model, dict(self.default))


@pytest.fixture()
def app():
    app = create_app("testing")
    app.extensions["container"].line_client = FakeLineClient()
    app.extensions["container"].vision_client = FakeVisionClient()
    app.extensions["container"].er_client = FakeErClient()
    app.extensions["container"].llm_client = FakeLlmClient()
    # 全測試期間保持一個 app context，讓測試可以在 client 請求之外直接呼叫
    # container 內的 repository（沿用既有測試寫法）。這顆 context 的物件參考
    # 交給 `client` fixture，讓它在每次送出請求前後暫時 pop/push，避免 Flask
    # 沿用同一個 app context 導致 flask.g（含 g.principal）跨請求殘留。
    ctx = app.app_context()
    ctx.push()
    app.extensions["_test_app_ctx"] = ctx
    db.create_all()
    yield app
    db.session.remove()
    db.drop_all()
    ctx.pop()


@pytest.fixture()
def client(app):
    ctx = app.extensions["_test_app_ctx"]
    test_client = app.test_client()
    original_open = test_client.open

    def _open_with_fresh_context(*args, **kwargs):
        ctx.pop()
        try:
            return original_open(*args, **kwargs)
        finally:
            ctx.push()

    test_client.open = _open_with_fresh_context
    return test_client


@pytest.fixture()
def container(app):
    return app.extensions["container"]


@pytest.fixture(autouse=True)
def hospitals(app):
    """多數測試假設 H1、H2 兩家院所存在。"""
    repo = app.extensions["container"].hospital_repo
    for hid, name in (("H1", "第一醫院"), ("H2", "第二診所")):
        repo.create(id=hid, name=name, region="台北市")
    return repo


@pytest.fixture()
def make_user(container):
    def _make(
        role: Role = Role.GUEST,
        binding: BindingStatus = BindingStatus.UNBOUND,
        hospital_id=None,
        line_user_id=None,
    ):
        user = UserRecord(
            id=UserRecord.new_id(),
            line_user_id=line_user_id or f"U{UserRecord.new_id()[:8]}",
            role=role,
            binding_status=binding,
            hospital_id=hospital_id,
        )
        return container.user_repo.save(user)

    return _make


@pytest.fixture()
def auth_header(container):
    def _header(user) -> dict[str, str]:
        principal = container.users.to_principal(user)
        return {"Authorization": f"Bearer {container.jwt.issue(principal).access_token}"}

    return _header


@pytest.fixture()
def line_signature(app):
    def _sign(body: str) -> str:
        secret = app.config["LINE_CHANNEL_SECRET"].encode()
        return base64.b64encode(hmac.new(secret, body.encode(), hashlib.sha256).digest()).decode()

    return _sign


@pytest.fixture()
def post_line_event(client, line_signature):
    def _post(event: dict):
        body = json.dumps({"destination": "Uxxx", "events": [event]})
        return client.post(
            "/line/webhook",
            data=body,
            headers={"X-Line-Signature": line_signature(body), "Content-Type": "application/json"},
        )

    return _post


@pytest.fixture()
def principal_factory():
    def _make(role: Role, **kw) -> Principal:
        return Principal(user_id="u1", role=role, **kw)

    return _make
