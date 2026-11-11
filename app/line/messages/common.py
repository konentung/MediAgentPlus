"""共用訊息：需綁定提示、院所確認、問題回報、預設回覆。"""

from __future__ import annotations

from linebot.v3.messaging import Message

from app.domain.enums import BindingStatus, Intent
from app.line.intents import postback_data
from app.line.messages.builder import MessageBuilder as M

FALLBACK_TEXT = "我不太確定你的意思，可以從下方選單選擇服務，或用自己的話再描述一次。"
FEEDBACK_TEXT = (
    "這裡沒有真人客服，但你的意見我們都會看。\n"
    "功能問題、內容有誤或建議，都可以留言給院方與開發團隊。"
)


def binding_required_messages(liff_url: str, feature: str) -> list[Message]:
    bubble = M.button_bubble(
        title="需先完成基本資料綁定",
        body=f"「{feature}」為個人化功能，需填寫基本資料並完成驗證後才能使用。",
        buttons=[{"type": "uri", "label": "前往填寫", "uri": liff_url}],
    )
    return [M.flex("需先完成基本資料綁定", bubble)]


INTAKE_TEXT: dict[BindingStatus, str] = {
    BindingStatus.UNBOUND: "填寫基本資料並選擇你就醫的院所，經院所核對後即可使用個人化衛教。",
    BindingStatus.PENDING: "基本資料已送出，等待院所核對中。你可以查看內容，或加入其他就醫院所。",
    BindingStatus.VERIFIED: "基本資料已通過院所核對。你可以查看填寫內容，或管理就醫院所。",
}


def intake_messages(intake_url: str, hospitals_url: str, status: BindingStatus) -> list[Message]:
    """基本資料入口：一張 Flex，兩顆按鈕分別開「基本資料表」與「我的就醫院所」。"""
    bubble = M.button_bubble(
        title="基本資料",
        body=INTAKE_TEXT.get(status, INTAKE_TEXT[BindingStatus.UNBOUND]),
        buttons=[
            {"type": "uri", "label": "基本資料表", "uri": intake_url},
            {"type": "uri", "label": "我的就醫院所", "uri": hospitals_url},
        ],
    )
    return [M.flex("基本資料", bubble)]


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
        title="問題回報",
        body=FEEDBACK_TEXT,
        buttons=[{"type": "uri", "label": "填寫問題回報", "uri": liff_url}],
    )
    return [M.flex("問題回報", bubble)]
