"""紅旗冷卻：緊急畫面在冷卻時間內只送一次。

紅旗關鍵字是不等模型的安全層，但用「包含」比對：使用者接著描述「胸痛好一點了」、「中風後要吃什麼藥」
每一句都會再命中，機器人就一直重傳同一則緊急畫面。這裡用 `conversation_state` 記下「已送過緊急畫面」
（獨立 key，不影響等照片 / 等位置的狀態），冷卻期間內：
- 含紅旗關鍵字的文字不再短路，照一般流程交給 AI 依脈絡判斷
- AI 仍判斷 emergency 時，只回它的一句話 + 撥打 119 / 找醫院按鈕，不重貼固定文案
"""

from __future__ import annotations

from app.line.context import BotContext

EMERGENCY_NOTIFIED = "emergency_notified"
COOLDOWN_SECONDS = 600


def _key(ctx: BotContext) -> str | None:
    return f"{ctx.line_user_id}:emergency" if ctx.line_user_id else None


def recently_notified(ctx: BotContext) -> bool:
    """冷卻時間內已經送過緊急畫面。"""
    key = _key(ctx)
    return bool(key and ctx.container.conversation_state.peek(key))


def mark_notified(ctx: BotContext) -> None:
    key = _key(ctx)
    if key:
        ctx.container.conversation_state.set(key, EMERGENCY_NOTIFIED, ttl=COOLDOWN_SECONDS)
