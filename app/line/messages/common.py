"""共用訊息：緊急畫面、需綁定提示、意見回饋、預設回覆。"""

from __future__ import annotations

from linebot.v3.messaging import Message, QuickReplyItem

from app.domain.enums import Intent
from app.line.intents import postback_data
from app.line.messages.builder import MessageBuilder as M

EMERGENCY_TEXT = "🚨 這可能是緊急狀況，請立即撥打 119。\n若你能移動，請前往最近的急救責任醫院。"
FALLBACK_TEXT = "我不太確定你的意思，可以從下方選單選擇服務，或用自己的話再描述一次。"
FEEDBACK_TEXT = (
    "這裡沒有真人客服，但你的意見我們都會看。\n"
    "功能問題、內容有誤或建議，都可以留言給院方與開發團隊。"
)


def emergency_items() -> list[QuickReplyItem]:
    return [
        M.uri_item("撥打 119", "tel:119"),
        M.postback_item("最近急救責任醫院", Intent.FIND_HOSPITAL, emergency="1"),
    ]


def emergency_messages() -> list[Message]:
    return [M.text(EMERGENCY_TEXT, quick_replies=emergency_items())]


def binding_required_messages(liff_url: str, feature: str) -> list[Message]:
    bubble = M.button_bubble(
        title="需先完成基本資料綁定",
        body=f"「{feature}」為個人化功能，需填寫基本資料並完成驗證後才能使用。",
        buttons=[{"type": "uri", "label": "前往填寫", "uri": liff_url}],
    )
    return [M.flex("需先完成基本資料綁定", bubble)]


def locked_messages() -> list[Message]:
    return [M.text("你的帳號目前已鎖定，請於就診時向院所人員說明，由院方核對後解除。")]


def fallback_messages() -> list[Message]:
    return [
        M.text(
            FALLBACK_TEXT,
            quick_replies=[M.postback_item("主選單", Intent.MENU)],
        )
    ]


def hospital_confirm_messages(hospital_name: str, hospital_id: str) -> list[Message]:
    """院方請病人確認就醫院所：病人自己按「是」之後，該院所才能核對建檔。"""
    bubble = M.button_bubble(
        title="請確認就醫院所",
        body=(
            f"「{hospital_name}」想為你核對基本資料並建檔。\n請問你目前是否在這家醫院 / 診所就醫？"
        ),
        buttons=[
            {
                "type": "postback",
                "label": "是，我在這裡就醫",
                "data": postback_data(
                    Intent.HOSPITAL_CONFIRM, hospital_id=hospital_id, answer="yes"
                ),
                "displayText": f"是，我在{hospital_name}就醫",
            },
            {
                "type": "postback",
                "label": "不是",
                "data": postback_data(
                    Intent.HOSPITAL_CONFIRM, hospital_id=hospital_id, answer="no"
                ),
                "displayText": "不是",
            },
        ],
    )
    return [M.flex("請確認就醫院所", bubble)]


def feedback_messages(liff_url: str) -> list[Message]:
    bubble = M.button_bubble(
        title="意見回饋",
        body=FEEDBACK_TEXT,
        buttons=[{"type": "uri", "label": "填寫意見回饋", "uri": liff_url}],
    )
    return [M.flex("意見回饋", bubble)]
