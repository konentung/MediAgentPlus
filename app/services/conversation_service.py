"""AI 對話助理（RAG + 事件判斷）。

流程：使用者自由文字 → 組脈絡（使用者狀態、最近對話、知識庫片段、即時資料）
→ OpenAI 依 `AssistantDecision` 結構回覆（answer 只能依片段生成，並標 citations）
→ 對回片段出處掛在 `decision.sources` → `AssistantHandler` 依 event 套用既有 LINE 模組。
這裡不產生任何 LINE 訊息，也不做規則式的意圖判斷；判斷交給模型，回應交給模組。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.exceptions import ExternalServiceError
from app.domain import triage_rules
from app.domain.assistant_schema import MAX_ANSWER_CHARS, AssistantDecision
from app.domain.principal import Principal
from app.domain.retrieval import summarize_hits, tokenize
from app.line.state_store import ConversationStateStore
from app.repositories.patient_record_repository import PatientRecordRepository
from app.repositories.patient_repository import PatientRepository
from app.services.knowledge_service import KnowledgeService
from app.services.pharmacy_service import PharmacyService
from app.services.triage_service import TriageService

logger = logging.getLogger(__name__)

HISTORY_ACTION = "history"
MAX_TEXT_CHARS = 1000
ASSISTANT_HISTORY_CHARS = 120  # 新增：對話紀錄裡助理回答只留這麼多字
_CJK = re.compile(r"[一-鿿]")
_CARE_HINTS = ("出院", "衛教", "照護", "回家", "注意", "傷口", "復健", "回診")


def _asks_care(text: str) -> bool:
    """像是在問出院後照護（只用來決定檢索要不要補上診斷，不是意圖判斷）。"""
    return any(k in text for k in _CARE_HINTS)


SYSTEM_PROMPT = """你是「MediAgent+」醫療照護 LINE 機器人的對話助理，服務台灣的病人與家屬，
只用繁體中文。你的工作是判斷使用者這則訊息屬於哪種事件、評估風險，
並依「知識庫片段」與「即時資料」給回覆。

回答（answer）的規則：
1. 只能根據「知識庫片段」與「即時資料」寫，逐條對照片段內容，不要加入片段沒有的說法、數字或藥名；
   片段之間有出入時採保守說法並說明「各院作法可能不同」。
2. 有依據就一定在 citations 列出用到的片段編號（K1、K2…）；沒有片段能回答時，answer 要明說
   「知識庫沒有這方面的資料」，並建議詢問原院所或就近就醫，不要憑常識補答。
3. 內容要具體可行：先一句結論，再列 2–4 個重點，最後視情況提醒何時該就醫。{max_chars} 字內、口語。
4. answer 是直接顯示在 LINE 聊天室的純文字，LINE 不會渲染 Markdown，符號會原樣出現在使用者畫面上：
   不要用 **粗體**、*斜體*、# 標題、`程式碼`、> 引言、表格、[文字](網址)，
   也不要用「- 」「* 」「1. 」開頭的清單。重點一律用「・」開頭、每點自己一行（用換行分開，
   不要全部接在同一行）；不要寫網址（參考來源由系統另外附上）。
5. 不做診斷、不開藥、不建議劑量；症狀問題只說明可能需要哪一科、何時該就醫、何時該撥 119。
6. 有生命危險的紅旗（胸痛、呼吸困難、意識不清、大量出血、中風徵兆、嚴重過敏、自殺意念）→
   event=emergency、risk=emergency，answer 只要一句話請對方立即撥 119。

事件與其他欄位：
7. 功能型事件（找醫院、找藥局、服藥提醒、出院衛教、基本資料）answer 可以很短，甚至留空，
   讓對應功能模組接手。
8. actions 是給使用者的下一步按鈕（最多 4 個），只選真的有幫助的。
9. 使用者提到縣市 / 藥名 / 症狀時填進 slots，讓功能模組直接使用。
   問某個藥是什麼、哪一類、怎麼吃 → event=medication、slots.drug_name 填藥名（照使用者寫的），
   「即時資料」有健保用藥資料時可用它說明藥名 / 類別 / 成分（不講劑量），模組會再附上查詢結果。
10. 使用者說想找真人、客訴、反映問題，或問題超出範圍且知識庫沒有答案 → event=feedback，
   answer 一句話說明目前沒有真人客服、可留下意見回饋或聯絡原院所。

病況諮詢（event=symptom）：
11. 判斷「可能需要掛哪一科」填入 slots.department（用衛福部科別名稱：家庭醫學科、內科、外科、
    兒科、婦產科、骨科、眼科、耳鼻喉科、皮膚科、泌尿科、神經科、精神科、復健科）：片段有提到以片段為準，
    片段沒提就依症狀本身選最接近的一科，判斷不出來填家庭醫學科（這個欄位只給系統找診所用，不算回答內容）。
    並用 risk 分級：none=可先自我照護、watch=建議近日就近到診所看診、urgent=請儘快到急診。
    answer 先講分級結論與科別，再列該注意的重點。none / watch 時系統會自動接著推薦附近有該科的診所，
    actions 不用放 find_clinic；使用者在對話裡說了自己現在所在的縣市 / 鄉鎮市區才填 slots.city /
    slots.district，沒說就留空（不要拿「居住地」代填，系統會請對方分享位置）。
    urgent → actions 放 find_hospital（急診看板）。
    已經給了建議（answer 有內容）就不要把 needs_clarification 設為 true。

找診所（event=clinic）與找醫院（event=hospital）的差別：
12. 使用者說要找診所、想就近看醫生、非急症要去哪看（「先看診所」「附近有診所嗎」「哪裡可以看」）→
    event=clinic，系統會從院所資料挑診所；event=hospital 只用在要找急診 / 大醫院 / 問急診壅塞狀況。
    「最近對話」裡使用者說過症狀時，把那個症狀填進 slots.symptom、建議科別填進 slots.department
    （例如先前說肚子痛，這則說「先看診所」→ slots.symptom=肚子痛、slots.department=家庭醫學科），
    answer 留空即可。

衛教諮詢（event=discharge）：
13. 「病人資料」有最近的就診 / 出院診斷時，answer 要對照片段寫出「現在該做的具體動作」：
    今日照護 2–4 條（傷口 / 飲食 / 活動 / 服藥）、什麼情況要回診、什麼情況撥 119，
    並以病人的診斷與慢性病為準，不要泛泛而談。
    沒有「病人資料」但使用者說了科別或疾病（例如「我剛從心臟內科出院」）→ 依片段寫該科 / 該病
    通用的出院照護重點，一樣要有 citations；片段完全沒有相關內容就照規則 2 明說。
    兩者都沒有（只說「我剛出院」、沒有科別也沒有診斷）才把 answer 留空，讓通用版衛教模組接手。

最近對話（history）：
14. 「最近對話」只用來理解代名詞與延續話題（「那要吃多久？」指的是上一則提到的藥），
    **不要重述、摘要或延伸先前的回答**；answer 只回應「使用者這則訊息」。
    這則訊息換了話題就完全忽略先前對話；不要主動提起使用者沒再問的舊話題。
""".replace("{max_chars}", str(MAX_ANSWER_CHARS))


@dataclass
class ConversationContext:
    user_status: str
    history: list[dict[str, str]]
    hits: list[dict[str, Any]]
    live_blocks: list[str] = field(default_factory=list)
    preferred_hospital_ids: list[str] = field(default_factory=list)  # 檢索時優先的院所（本院）
    patient_block: str | None = None  # 已驗證病人的診斷 / 慢性病（衛教個人化用）


class ConversationService:
    def __init__(
        self,
        knowledge: KnowledgeService,
        pharmacies: PharmacyService,
        triage: TriageService,
        patients: PatientRepository,
        states: ConversationStateStore,
        llm: Callable[[], Any],
        *,
        records: PatientRecordRepository | None = None,
        enabled: bool = True,
        history_turns: int = 6,
        history_ttl: int = 1800,
        top_k: int = 4,
    ) -> None:
        self._knowledge = knowledge
        self._pharmacies = pharmacies
        self._triage = triage
        self._patients = patients
        self._records = records
        self._states = states
        self._llm = llm
        self.enabled = enabled
        self._history_turns = history_turns
        self._history_ttl = history_ttl
        self._top_k = top_k

    # ---- 主流程 ----

    def decide(self, principal: Principal, text: str) -> AssistantDecision:
        """組脈絡 → 呼叫模型 → 驗證結構 → 掛上引用片段。失敗一律 raise ExternalServiceError。"""
        if not self.enabled:
            raise ExternalServiceError("AI 助理未啟用")
        text = text.strip()[:MAX_TEXT_CHARS]
        context = self.build_context(principal, text)
        prompt = self.build_prompt(text, context)
        decision = self._llm().complete_structured(prompt, AssistantDecision, system=SYSTEM_PROMPT)
        decision.answer = decision.answer.strip()[:MAX_ANSWER_CHARS]
        decision.actions = list(dict.fromkeys(decision.actions))[:4]
        decision.attach_sources(context.hits)
        self._remember(principal, "user", text)
        if decision.answer:
            self._remember(principal, "assistant", decision.answer, event=decision.event)
        return decision

    # ---- 脈絡 ----

    def build_context(self, principal: Principal, text: str) -> ConversationContext:
        profile = (
            self._patients.get_profile(principal.user_id) if principal.user_id else None
        ) or {}
        # 修改：衛教資訊是不分院所的共用池，所有人都查全部；
        # 有院所（所屬院所 / 就醫院所）的人，本院夠相關的片段優先。
        own = list(
            dict.fromkeys(
                h for h in (principal.hospital_id, profile.get("preferred_hospital_id")) if h
            )
        )
        patient_block, diagnosis, department = self._patient_block(principal, profile)
        # 出院衛教問題通常不會寫出診斷名稱（「我回家要注意什麼」），檢索時補上診斷才找得到對應衛教；
        # 最近就診的科別拿來替同科別的衛教片段加權（不硬過濾）。
        # 修改：使用者自己說出科別（通用版出院衛教選科別、「我剛從骨科出院」）時也用它加權
        # 科別名稱先比對知識庫實際用到的（各院所自己的分類），就診紀錄的科別也經同一套比對
        department = self._knowledge.detect_department([text, department], None) or department
        query = f"{text} {diagnosis}" if diagnosis and _asks_care(text) else text
        hits = self._knowledge.retrieve(
            query, top_k=self._top_k, department=department, prefer_hospital_ids=own
        )
        live: list[str] = []
        er_block = self._er_block(text, profile)
        if er_block:
            live.append(er_block)
        drug_block = self._drug_block(text)
        if drug_block:
            live.append(drug_block)
        return ConversationContext(
            user_status=self._user_status(principal, profile),
            history=self._history(principal),
            hits=hits,
            live_blocks=live,
            preferred_hospital_ids=own,
            patient_block=patient_block,
        )

    def build_prompt(self, text: str, context: ConversationContext) -> str:
        parts = [f"【使用者狀態】{context.user_status}"]
        if context.patient_block:
            parts.append("【病人資料】\n" + context.patient_block)
        if context.history:
            lines = [
                f"{'使用者' if t['role'] == 'user' else '助理'}：{t['text']}"
                for t in context.history
            ]
            # 修改：標明只供理解脈絡，模型不該把舊回答再講一次
            parts.append("【最近對話】（僅供理解上下文，不要重複其中內容）\n" + "\n".join(lines))
        parts.append("【知識庫片段】\n" + (summarize_hits(context.hits) or "（沒有相關片段）"))
        if context.live_blocks:
            parts.append("【即時資料】\n" + "\n".join(context.live_blocks))
        parts.append(f"【使用者這則訊息】\n{text}")
        return "\n\n".join(parts)

    @staticmethod
    def _user_status(principal: Principal, profile: dict[str, Any]) -> str:
        status = {
            "unbound": "尚未填寫基本資料（不能用個人化功能，但找醫院 / 找藥局 / 服藥提醒都可以用）",
            "pending": "基本資料已送出、等待院所核對",
            "verified": "基本資料已驗證，可使用個人化衛教",
            "locked": "帳號已鎖定，請於就診時請院所人員核對解鎖",
        }.get(principal.binding_status.value, principal.binding_status.value)
        city = profile.get("address_city")
        extra = f"；居住地 {city}{profile.get('address_district') or ''}" if city else ""
        return f"{status}{extra}"

    def _patient_block(
        self, principal: Principal, profile: dict[str, Any]
    ) -> tuple[str | None, str | None, str | None]:
        """已驗證病人：最近一次就診（科別 / 診斷 / 出院代碼）+ 慢性病 → 衛教個人化脈絡。

        回 (區塊文字, 診斷摘要, 就診科別)；未驗證或沒有院方紀錄回 (None, None, None)，
        模型會改走通用版。科別另外用來替同科別的衛教片段加權。
        """
        if not (principal.is_verified and principal.user_id and self._records):
            return None, None, None
        encounter = self._records.latest_encounter(principal.user_id)
        lines: list[str] = []
        diagnosis = ""
        department = (encounter or {}).get("department") or None
        if encounter:
            diagnosis = "、".join(
                d.get("description") or d.get("icd10_code") or ""
                for d in (encounter.get("diagnoses") or [])
                if isinstance(d, dict)
            ).strip("、")
            kind = {"opd": "門診", "er": "急診", "inpatient": "住院"}.get(
                encounter.get("encounter_type") or "", "就診"
            )
            when = encounter.get("discharged_at") or encounter.get("visit_date") or "?"
            code = encounter.get("discharge_code")
            lines.append(
                f"最近{kind}：{when}，科別 {encounter.get('department') or '未填'}，"
                f"診斷 {diagnosis or '未填'}" + (f"，出院代碼 {code}" if code else "")
            )
            if encounter.get("patient_visible_summary"):
                lines.append(f"院方說明：{encounter['patient_visible_summary'][:200]}")
        chronic = profile.get("chronic_conditions") or []
        if chronic:
            lines.append("慢性病：" + "、".join(str(c) for c in chronic))
        if not lines:
            return None, None, None
        return "\n".join(f"・{line}" for line in lines), diagnosis or None, department

    def _er_block(self, text: str, profile: dict[str, Any]) -> str | None:
        """訊息或住址提到的縣市 → 該縣市急診看板摘要（用已存的資料，不即時抓）。"""
        normalized = text.replace("臺", "台")
        city = next((c for c in triage_rules.AREA_CODES if c in normalized), None)
        if city is None and any(k in normalized for k in ("急診", "醫院", "掛號", "急救")):
            city = (profile.get("address_city") or "").replace("臺", "台") or None
        code = triage_rules.AREA_CODES.get(city or "")
        if code is None:
            return None
        board = self._triage.board(area_code=code, refresh_if_stale=False)
        if not board["hospitals"]:
            return None
        lines = [
            f"{h['hospital_name']}（{h['tier_label']}）{h['level_label']}，"
            f"等住院 {h['wait_admit']}、等 ICU {h['wait_icu']}"
            + ("，119 滿床通報" if h.get("full_119") else "")
            for h in board["hospitals"][:6]
        ]
        stale = "，資料可能延遲" if board.get("stale") else ""
        return (
            f"{city} 急診即時狀況（更新 {board.get('reported_at_label') or '?'}{stale}）：\n"
            + "\n".join(f"・{line}" for line in lines)
        )

    def _drug_block(self, text: str) -> str | None:
        """訊息裡像藥名的詞 → 健保用藥主檔查詢結果（讓模型知道這是什麼藥、屬哪一類）。"""
        found: list[str] = []
        seen: set[str] = set()
        # 修改：中文商品名（普拿疼、保栓通）jieba 切不出來，先讓資料庫直接比對句子裡出現的藥名
        for drug in self._pharmacies.mentioned_drugs(text):
            key = (drug.get("name_zh") or drug["name_en"]).lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(self._drug_line(drug))
        for token in tokenize(text):
            if len(found) >= 3:
                break
            if (
                token in seen
                or (token.isascii() and len(token) < 4)
                or (_CJK.search(token) and len(token) < 3)  # 兩字詞（糖尿、頭痛）太容易誤配藥名
            ):
                continue
            seen.add(token)
            for drug in self._pharmacies.search_drugs(token, limit=1):
                names = (drug.get("name_en") or "", drug.get("name_zh") or "")
                if not any(n.lower().startswith(token) for n in names if n):
                    continue  # 只收名稱開頭就是這個詞的結果，避免「頭痛」撈到「治偏頭痛錠」
                key = (drug.get("name_zh") or drug["name_en"]).lower()
                if key not in seen:
                    seen.add(key)
                    found.append(self._drug_line(drug))
        return "健保用藥資料：\n" + "\n".join(f"・{f}" for f in found[:3]) if found else None

    @staticmethod
    def _drug_line(drug: dict[str, Any]) -> str:
        name = drug.get("name_zh") or drug["name_en"]
        category = f"（{drug['category']}）" if drug.get("category") else ""
        return f"{name} / {drug['name_en']}{category}，成分 {drug.get('ingredient') or '?'}"

    # ---- 對話紀錄（短期，存在對話狀態 store）----

    def _history_key(self, principal: Principal) -> str | None:
        return f"{principal.line_user_id}:history" if principal.line_user_id else None

    def _history(self, principal: Principal) -> list[dict[str, str]]:
        key = self._history_key(principal)
        state = self._states.peek(key) if key else None
        turns = (state.payload.get("turns") if state else None) or []
        return turns[-self._history_turns :]

    def _remember(
        self, principal: Principal, role: str, text: str, event: str | None = None
    ) -> None:
        key = self._history_key(principal)
        if not key:
            return
        turns = self._history(principal)
        # 修改：助理的回答只留開頭（夠認出話題就好），整段存進去模型容易照抄舊內容
        limit = 300 if role == "user" else ASSISTANT_HISTORY_CHARS
        turns.append({"role": role, "text": text[:limit], **({"event": event} if event else {})})
        self._states.set(
            key, HISTORY_ACTION, {"turns": turns[-self._history_turns :]}, ttl=self._history_ttl
        )

    def clear_history(self, principal: Principal) -> None:
        key = self._history_key(principal)
        if key:
            self._states.clear(key)
