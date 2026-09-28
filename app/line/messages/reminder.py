"""服藥小幫手訊息：選單、排程清單、到期提醒（推播）、慢箋領藥提醒。"""

from __future__ import annotations

from typing import Any

from linebot.v3.messaging import Message

from app.domain.enums import Intent
from app.domain.reminder_rules import FREQUENCY_LABELS, TIMING_LABELS
from app.line.intents import postback_data
from app.line.messages.builder import MessageBuilder as M

DISCLAIMER = "以上依處方箋內容整理，用藥疑問請洽藥師或原院所。"
STATUS_LABELS = {"active": "進行中", "paused": "暫停中", "finished": "已結束", "stopped": "已停用"}


def menu_messages(liff_url: str) -> list[Message]:
    return [
        M.text(
            "服藥小幫手：你想做什麼？",
            quick_replies=[
                M.postback_item("查看提醒", Intent.REMINDER, action="list"),
                M.uri_item("新增 / 編輯提醒", liff_url),
                M.postback_item("拍處方箋建立提醒", Intent.MEDICATION, action="photo"),
                M.postback_item("回報副作用", Intent.HUMAN, reason="side_effect"),
            ],
        )
    ]


def list_messages(schedules: list[dict[str, Any]], liff_url: str) -> list[Message]:
    if not schedules:
        bubble = M.button_bubble(
            title="還沒有服藥提醒",
            body="拍下處方箋讓我幫你建立提醒，或到提醒頁手動新增。",
            buttons=[
                {"type": "uri", "label": "開啟提醒頁", "uri": liff_url},
                {
                    "type": "postback",
                    "label": "拍處方箋",
                    "data": postback_data(Intent.MEDICATION, action="photo"),
                },
            ],
        )
        return [M.flex("服藥提醒", bubble)]
    rows = []
    for s in schedules[:10]:
        times = "、".join(s.get("times") or []) or "未設定時間"
        status = STATUS_LABELS.get(s.get("status", "active"), "")
        rows.append(
            {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": s["drug_name"],
                        "weight": "bold",
                        "size": "sm",
                        "wrap": True,
                    },
                    {
                        "type": "text",
                        "text": f"{times}　{status}",
                        "size": "xs",
                        "color": "#888888",
                        "wrap": True,
                    },
                ],
            }
        )
    bubble = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#2F6F62",
            "contents": [
                {"type": "text", "text": "我的服藥提醒", "weight": "bold", "color": "#FFFFFF"}
            ],
        },
        "body": {"type": "box", "layout": "vertical", "contents": rows},
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#2F6F62",
                    "action": {"type": "uri", "label": "管理提醒", "uri": liff_url},
                }
            ],
        },
    }
    return [M.flex("我的服藥提醒", bubble)]


def due_messages(items: list[dict[str, Any]], liff_url: str) -> list[Message]:
    """到期提醒：同一人同一批多顆藥合成一則；每顆藥各有「已服用 / 稍後 / 跳過」按鈕。"""
    time_label = items[0]["time_label"] if items else ""
    contents: list[dict[str, Any]] = [
        {"type": "text", "text": f"⏰ {time_label} 該吃藥了", "weight": "bold", "size": "lg"},
    ]
    for item in items:
        dose = f"　{item['dose_text']}" if item.get("dose_text") else ""
        contents.append(
            {
                "type": "box",
                "layout": "vertical",
                "margin": "lg",
                "contents": [
                    {
                        "type": "text",
                        "text": f"{item['drug_name']}{dose}",
                        "wrap": True,
                        "size": "sm",
                    },
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "spacing": "sm",
                        "margin": "sm",
                        "contents": [
                            _small_button("✅ 已服用", "taken", item["delivery_id"]),
                            _small_button("⏰ 30 分後", "snooze", item["delivery_id"]),
                            _small_button("跳過", "skip", item["delivery_id"], secondary=True),
                        ],
                    },
                ],
            }
        )
    contents.append(
        {
            "type": "text",
            "text": DISCLAIMER,
            "size": "xxs",
            "color": "#999999",
            "wrap": True,
            "margin": "lg",
        }
    )
    bubble = {
        "type": "bubble",
        "body": {"type": "box", "layout": "vertical", "contents": contents},
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "button",
                    "style": "link",
                    "height": "sm",
                    "action": {"type": "uri", "label": "查看所有提醒", "uri": liff_url},
                }
            ],
        },
    }
    names = "、".join(i["drug_name"] for i in items)[:60]
    return [M.flex(f"服藥提醒 {time_label}：{names}", bubble)]


def _small_button(label: str, action: str, delivery_id: str, secondary: bool = False) -> dict:
    button: dict[str, Any] = {
        "type": "button",
        "style": "secondary" if secondary else "primary",
        "height": "sm",
        "action": {
            "type": "postback",
            "label": label,
            "data": postback_data(Intent.REMINDER, action=action, delivery_id=delivery_id),
            "displayText": label,
        },
    }
    if not secondary:
        button["color"] = "#2F6F62"
    return button


def refill_messages(prescription: dict[str, Any], liff_url: str) -> list[Message]:
    seq = prescription.get("refill_seq") or 0
    total = prescription.get("refill_total") or 0
    hospital = prescription.get("hospital_name") or "原院所"
    bubble = M.button_bubble(
        title="慢性病連續處方箋 領藥提醒",
        body=(
            f"你在 {hospital} 的慢箋（第 {seq}/{total} 次）快用完了，"
            "這幾天可以持處方箋到健保特約藥局或原院所領下一次的藥。"
        ),
        buttons=[
            {
                "type": "postback",
                "label": "找附近藥局",
                "data": postback_data(
                    Intent.MEDICATION, action="locate_prompt", prescription_id=prescription["id"]
                ),
            },
            {"type": "uri", "label": "查看處方內容", "uri": liff_url},
        ],
    )
    return [M.flex("慢箋領藥提醒", bubble)]


def response_text(action: str, drug_name: str) -> str:
    return {
        "taken": f"已記錄「{drug_name}」服用完成 👍",
        "snooze": f"好的，30 分鐘後再提醒你吃「{drug_name}」。",
        "skip": f"已略過這次「{drug_name}」的提醒。",
    }.get(action, "已更新。")


def frequency_label(code: str | None) -> str:
    return FREQUENCY_LABELS.get(code or "other", "其他")


def timing_label(code: str | None) -> str:
    return TIMING_LABELS.get(code or "", "")
