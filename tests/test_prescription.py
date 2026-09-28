"""處方箋：LINE 拍照辨識流程（對話狀態、loading 動畫、Flex 結果）、LIFF 修改 / 確認 API、照片保留。"""

from __future__ import annotations

from pathlib import Path

import pytest
from linebot.v3.messaging import FlexMessage, TextMessage

from app.core.exceptions import ExternalServiceError
from app.domain.enums import Intent
from app.line.intents import postback_data
from app.line.state_store import AWAIT_LOCATION, AWAIT_PRESCRIPTION_PHOTO
from tests.conftest import sample_extraction
from tests.test_pharmacy import DRUG_ROWS, NHI_ROWS


def _event(event_type: str, user_id: str = "U1", **extra) -> dict:
    return {
        "type": event_type,
        "mode": "active",
        "timestamp": 1700000000000,
        "source": {"type": "user", "userId": user_id},
        "webhookEventId": "01",
        "deliveryContext": {"isRedelivery": False},
        "replyToken": "rt",
        **extra,
    }


def _image_event(user_id="U1", message_id="img1"):
    return _event(
        "message",
        user_id=user_id,
        message={
            "type": "image",
            "id": message_id,
            "contentProvider": {"type": "line"},
            "quoteToken": "q",
        },
    )


def _location_event(address, user_id="U1"):
    return _event(
        "message",
        user_id=user_id,
        message={
            "type": "location",
            "id": "loc1",
            "title": "我的位置",
            "address": address,
            "latitude": 25.05,
            "longitude": 121.55,
        },
    )


def _quick_labels(message) -> list[str]:
    return [
        item.action.label for item in (message.quick_reply.items if message.quick_reply else [])
    ]


def test_menu_sets_state_and_offers_camera(post_line_event, container):
    post_line_event(_event("postback", postback={"data": postback_data(Intent.MEDICATION)}))
    _, messages = container.line_client.replies[-1]
    labels = _quick_labels(messages[0])
    assert any("拍處方箋" in lb for lb in labels) and any("從相簿" in lb for lb in labels)
    assert container.conversation_state.peek("U1").action == AWAIT_PRESCRIPTION_PHOTO


def test_image_is_recognized_when_waiting_for_photo(post_line_event, container):
    container.pharmacies.import_drugs(DRUG_ROWS)
    post_line_event(_event("postback", postback={"data": postback_data(Intent.MEDICATION)}))
    post_line_event(_image_event())

    line = container.line_client
    assert line.loading == [("U1", 60)]  # 用 loading 動畫取代「辨識中」訊息
    assert line.downloaded == ["img1"]
    assert container.vision_client.calls == [(len(line.get_message_content("x")), "image/png")]
    _, messages = line.replies[-1]
    assert isinstance(messages[0], FlexMessage) and "辨識結果" in messages[0].alt_text
    assert container.conversation_state.peek("U1") is None

    user = container.user_repo.get_by_line_user_id("U1")
    rows = container.prescription_repo.list_for_user(user.id)
    assert len(rows) == 1
    p = rows[0]
    assert p["status"] == "draft" and p["hospital_name"] == "第一醫院"
    assert p["is_chronic_refill"] and p["refill_seq"] == 1 and p["refill_total"] == 3
    assert p["items"][0]["frequency_code"] == "bid" and p["items"][0]["timing"] == "pc"
    assert p["items"][0]["nhi_code"] == "BC23456100"  # 對到藥品主檔
    assert p["items"][0]["category"] == "消化道及代謝"
    assert p["items"][1]["frequency_code"] == "hs" and p["items"][1]["nhi_code"] is None
    # 照片完整保留（加密後），且 ocr_raw 不外露
    assert Path(p["image_path"]).exists()
    assert Path(p["image_path"]).read_bytes() != line.get_message_content("x")


def test_image_without_context_asks_first(post_line_event, container):
    post_line_event(_image_event(message_id="img9"))
    _, messages = container.line_client.replies[-1]
    assert isinstance(messages[0], TextMessage) and "處方箋" in messages[0].text
    assert container.vision_client.calls == []
    # 按「是，幫我辨識」→ 用 postback 內的 message_id 辨識
    post_line_event(
        _event(
            "postback",
            postback={"data": postback_data(Intent.MEDICATION, action="image", message_id="img9")},
        )
    )
    assert container.line_client.downloaded == ["img9"]
    assert len(container.vision_client.calls) == 1


def test_not_prescription_and_ocr_failure(post_line_event, container):
    container.vision_client.result = sample_extraction(is_prescription=False, items=[])
    post_line_event(_event("postback", postback={"data": postback_data(Intent.MEDICATION)}))
    post_line_event(_image_event())
    _, messages = container.line_client.replies[-1]
    assert "不是處方箋" in messages[0].text
    assert container.conversation_state.peek("U1").action == AWAIT_PRESCRIPTION_PHOTO  # 可直接重拍
    user = container.user_repo.get_by_line_user_id("U1")
    assert container.prescription_repo.list_for_user(user.id) == []

    container.vision_client.error = ExternalServiceError("處方辨識逾時")
    post_line_event(_image_event(message_id="img2"))
    _, messages = container.line_client.replies[-1]
    assert "逾時" in messages[0].text


def test_confirm_then_location_finds_pharmacies(post_line_event, container):
    container.pharmacies.import_pharmacies(NHI_ROWS)
    post_line_event(_event("postback", postback={"data": postback_data(Intent.MEDICATION)}))
    post_line_event(_image_event())
    user = container.user_repo.get_by_line_user_id("U1")
    pid = container.prescription_repo.list_for_user(user.id)[0]["id"]

    post_line_event(
        _event(
            "postback",
            postback={
                "data": postback_data(Intent.MEDICATION, action="confirm", prescription_id=pid)
            },
        )
    )
    assert container.prescription_repo.get(pid)["status"] == "confirmed"
    # 慢箋第 1/3 次、開立 9/20、28 天 → 10/18 前 3 天提醒
    assert container.prescription_repo.get(pid)["refill_reminder_on"] == "2026-10-15"
    state = container.conversation_state.peek("U1")
    assert state.action == AWAIT_LOCATION and state.payload["prescription_id"] == pid
    _, messages = container.line_client.replies[-1]
    assert "分享目前位置" in _quick_labels(messages[0])[0]

    post_line_event(_location_event("台灣台北市松山區南京東路四段133巷"))
    _, messages = container.line_client.replies[-1]
    assert "台北市松山區" in messages[0].text
    assert isinstance(messages[1], FlexMessage)
    carousel = messages[1].contents.to_dict()
    assert carousel["type"] == "carousel"
    assert carousel["contents"][0]["body"]["contents"][0]["text"] == "榮星藥局"
    assert container.conversation_state.peek("U1") is None


def test_location_without_context_then_choose_pharmacy(post_line_event, container):
    container.pharmacies.import_pharmacies(NHI_ROWS)
    post_line_event(_location_event("台北市信義區松仁路"))
    _, messages = container.line_client.replies[-1]
    assert "要找什麼" in messages[0].text
    post_line_event(
        _event(
            "postback",
            postback={"data": postback_data(Intent.MEDICATION, action="locate", source="state")},
        )
    )
    _, messages = container.line_client.replies[-1]
    assert "信義區" in messages[0].text and isinstance(messages[1], FlexMessage)


@pytest.mark.parametrize(
    "address",
    ["No. 133, Sec. 4, Nanjing E. Rd., Songshan District, Taipei City, Taiwan 105", None],
)
def test_location_with_english_or_missing_address_uses_coordinates(
    post_line_event, container, address
):
    """手機語系是英文（或 LINE 沒附地址）時，用座標反推鄉鎮市區，不能回「無法判斷縣市」。"""
    container.pharmacies.import_pharmacies(NHI_ROWS)
    post_line_event(
        _event(
            "postback", postback={"data": postback_data(Intent.MEDICATION, action="locate_prompt")}
        )
    )
    post_line_event(_location_event(address))
    _, messages = container.line_client.replies[-1]
    assert "台北市松山區" in messages[0].text and isinstance(messages[1], FlexMessage)

    # 先傳位置、再按「找附近藥局」的路徑也要帶座標
    post_line_event(_location_event(address))
    post_line_event(
        _event(
            "postback",
            postback={"data": postback_data(Intent.MEDICATION, action="locate", source="state")},
        )
    )
    _, messages = container.line_client.replies[-1]
    assert "台北市松山區" in messages[0].text and isinstance(messages[1], FlexMessage)


def test_drug_lookup_by_free_text(post_line_event, container):
    container.pharmacies.import_drugs(DRUG_ROWS)
    post_line_event(
        _event("postback", postback={"data": postback_data(Intent.MEDICATION, action="drug")})
    )
    post_line_event(
        _event(
            "message", message={"type": "text", "id": "m1", "text": "metformin", "quoteToken": "q"}
        )
    )
    _, messages = container.line_client.replies[-1]
    assert "Metformin" in messages[0].text
    # 狀態已清掉：再打一次自由文字就是 fallback
    post_line_event(
        _event(
            "message", message={"type": "text", "id": "m2", "text": "metformin", "quoteToken": "q"}
        )
    )
    _, messages = container.line_client.replies[-1]
    # 自由文字交給 AI 助理（FakeLlmClient 回 medication 事件），不再是關鍵字 fallback
    assert container.llm_client.calls and "降血糖" in messages[0].text


def test_prescription_liff_api(client, container, make_user, auth_header, post_line_event):
    post_line_event(_event("postback", postback={"data": postback_data(Intent.MEDICATION)}))
    post_line_event(_image_event())
    owner = container.user_repo.get_by_line_user_id("U1")
    pid = container.prescription_repo.list_for_user(owner.id)[0]["id"]
    headers = auth_header(owner)

    resp = client.get(f"/api/v1/liff/prescriptions/{pid}", headers=headers)
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["has_image"] and "ocr_raw" not in data and "image_path" not in data
    assert (
        client.get("/api/v1/liff/prescriptions", headers=headers).get_json()["data"][0]["id"] == pid
    )

    # 別人看不到
    other = make_user()
    assert (
        client.get(f"/api/v1/liff/prescriptions/{pid}", headers=auth_header(other)).status_code
        == 404
    )

    # 照片可以取回（解密後就是原圖）
    img = client.get(f"/api/v1/liff/prescriptions/{pid}/image", headers=headers)
    assert img.status_code == 200 and img.data == container.line_client.get_message_content("x")

    # 修改藥品清單
    items = data["items"]
    items[1]["drug_name"] = "Zolpidem 10mg"
    items[1]["frequency_code"] = "hs"
    resp = client.patch(
        f"/api/v1/liff/prescriptions/{pid}",
        json={"items": items, "days_supply": 30, "refill_seq": 2},
        headers=headers,
    )
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["data"]["items"][1]["drug_name"] == "Zolpidem 10mg"
    bad = client.patch(
        f"/api/v1/liff/prescriptions/{pid}",
        json={"items": [{"drug_name": "x", "frequency_code": "twice"}]},
        headers=headers,
    )
    assert bad.status_code == 422

    resp = client.post(f"/api/v1/liff/prescriptions/{pid}/confirm", headers=headers)
    assert resp.status_code == 200 and resp.get_json()["data"]["status"] == "confirmed"
    # 第 2/3 次、30 天 → 開立日 + 60 - 3
    assert resp.get_json()["data"]["refill_reminder_on"] == "2026-11-16"

    assert client.delete(f"/api/v1/liff/prescriptions/{pid}", headers=headers).status_code == 200
    assert client.get(f"/api/v1/liff/prescriptions/{pid}", headers=headers).status_code == 404
