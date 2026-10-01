"""RAG 檢索工具：中文斷詞（jieba）、BM25、切段、混合排序（RRF）、prompt 用的片段摘要。

PostgreSQL 上關鍵字檢索交給全文檢索（tsvector）、語意檢索交給 pgvector，兩者用 `rrf_fuse()` 合併；
`BM25Index` 只在 SQLite（測試）環境當關鍵字檢索的替代。
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import jieba

jieba.setLogLevel(60)

# jieba 預設字典偏簡體 / 通用語料，醫療與健保用語先補進去
CUSTOM_WORDS: tuple[str, ...] = tuple(
    (
        "慢箋 處方箋 藥局 急診 門診 掛號 衛教 回診 領藥 服藥 健保 健保卡 家醫科 神經內科 耳鼻喉科 "
        "腸胃科 心臟內科 泌尿科 皮膚科 小兒科 婦產科 檢傷 急救責任醫院 醫學中心 區域醫院 地區醫院 "
        "診所 頭痛 胸痛 發燒 腹瀉 飯前 飯後 睡前 副作用 過敏 出院 傷口 基本資料 意見回饋 服藥提醒"
    ).split()
)
for _word in CUSTOM_WORDS:
    jieba.add_word(_word)

_TOKEN_PATTERN = re.compile(r"[一-鿿]+|[a-z0-9][a-z0-9.+-]*")
_CJK = re.compile(r"[一-鿿]")
STOP_WORDS: frozenset[str] = frozenset(
    (
        "的 了 嗎 呢 吧 啊 我 你 他 她 它 我們 你們 是 在 有 沒有 不 也 都 要 想 請問 請 一下 可以 "
        "怎麼 什麼 哪 哪裡 哪個 這 那 這個 那個 就 還 會 跟 和 與 或 但 然後 因為 所以 如果 "
        "the a an is are to of and or"
    ).split()
)
_MIN_LEN = 2


def tokenize(text: str) -> list[str]:
    """中英混合斷詞：中文用 jieba 搜尋模式（含子詞）再加字元 bigram，英數轉小寫；去停用詞與單字。"""
    tokens: list[str] = []
    for piece in _TOKEN_PATTERN.findall(text.lower().replace("臺", "台")):
        if piece.isascii():
            candidates = [piece]
        else:
            candidates = jieba.lcut_for_search(piece)
            candidates += [piece[i : i + 2] for i in range(len(piece) - 1)]
        for token in candidates:
            token = token.strip()
            if len(token) < _MIN_LEN or token in STOP_WORDS:
                continue
            tokens.append(token)
    return tokens


_UNSAFE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ue000-\uf8ff]")


def clean_text(text: str) -> str:
    """去掉 PDF / DOCX 抽字常見的控制字元與私有區符號（PostgreSQL 不接受 NUL），保留換行。"""
    return _UNSAFE.sub("", text).replace("\r\n", "\n")


def cjk_ratio(text: str) -> float:
    return len(_CJK.findall(text)) / max(len(text), 1)


def content_hash(text: str) -> str:
    return hashlib.sha1(re.sub(r"\s+", " ", text).strip().encode()).hexdigest()


# 新增：衛教知識庫的科別（= `衛教內文_依科別分類/NN_<科別>.txt` 的檔名、
# `knowledge_documents.department`）。使用者文字裡出現這些名稱時可直接拿來做同科別加權；
# 長的先比對（「心臟血管外科」不能被「外科」搶走）。
KNOWLEDGE_DEPARTMENTS: tuple[str, ...] = (
    "心臟內科",
    "胸腔內科",
    "呼吸治療科",
    "肝膽腸胃內科",
    "腎臟內科",
    "內分泌新陳代謝科",
    "血液腫瘤科",
    "感染科",
    "風濕免疫過敏科",
    "神經內科",
    "家庭醫學科",
    "老年醫學科",
    "安寧緩和醫療科",
    "重症醫學科",
    "急診醫學科",
    "職業醫學科",
    "一般外科",
    "大腸直腸外科",
    "乳房外科",
    "心臟血管外科",
    "胸腔外科",
    "神經外科",
    "骨科",
    "泌尿科",
    "整形外科",
    "小兒外科",
    "婦科",
    "產科",
    "生殖醫學科",
    "兒科",
    "新生兒科",
    "眼科",
    "耳鼻喉科",
    "牙科",
    "皮膚科",
    "精神科",
    "復健科",
    "疼痛科",
    "麻醉科",
    "放射腫瘤科",
    "中醫科",
    "營養科",
)
_DEPARTMENT_ALIASES = {  # 口語 / 簡稱 → 知識庫科別
    "心臟科": "心臟內科",
    "腸胃科": "肝膽腸胃內科",
    "胃腸科": "肝膽腸胃內科",
    "肝膽腸胃科": "肝膽腸胃內科",
    "腎臟科": "腎臟內科",
    "新陳代謝科": "內分泌新陳代謝科",
    "內分泌科": "內分泌新陳代謝科",
    "神經科": "神經內科",
    "家醫科": "家庭醫學科",
    "外科": "一般外科",
    "小兒科": "兒科",
    "婦產科": "婦科",
    "身心科": "精神科",
    "中醫": "中醫科",
}


def department_in_text(text: str) -> str | None:
    """文字裡提到的知識庫科別（「我剛從心臟內科出院」→ 心臟內科）；沒有回 None。"""
    normalized = text.replace("臺", "台")
    for name in sorted(KNOWLEDGE_DEPARTMENTS, key=len, reverse=True):
        if name in normalized:
            return name
    for alias, name in sorted(_DEPARTMENT_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if alias in normalized:
            return name
    return None


@dataclass
class BM25Index:
    """Okapi BM25，只用於 SQLite 環境。`documents` 是 (id, tokens)。"""

    k1: float = 1.5
    b: float = 0.75
    _ids: list[str] = field(default_factory=list)
    _term_freqs: list[Counter] = field(default_factory=list)
    _lengths: list[int] = field(default_factory=list)
    _doc_freq: Counter = field(default_factory=Counter)
    _avg_len: float = 0.0

    @classmethod
    def build(cls, documents: list[tuple[str, list[str]]]) -> BM25Index:
        index = cls()
        for doc_id, tokens in documents:
            freqs = Counter(tokens)
            index._ids.append(doc_id)
            index._term_freqs.append(freqs)
            index._lengths.append(len(tokens))
            index._doc_freq.update(freqs.keys())
        index._avg_len = sum(index._lengths) / len(index._lengths) if index._lengths else 0.0
        return index

    def __len__(self) -> int:
        return len(self._ids)

    def search(
        self, query: str, *, top_k: int = 5, min_score: float = 0.0
    ) -> list[tuple[str, float]]:
        terms = tokenize(query)
        if not terms or not self._ids:
            return []
        n = len(self._ids)
        scores = [0.0] * n
        for term in set(terms):
            df = self._doc_freq.get(term)
            if not df:
                continue
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for i, freqs in enumerate(self._term_freqs):
                tf = freqs.get(term)
                if not tf:
                    continue
                norm = self.k1 * (1 - self.b + self.b * self._lengths[i] / (self._avg_len or 1))
                scores[i] += idf * tf * (self.k1 + 1) / (tf + norm)
        ranked = sorted(
            ((self._ids[i], s) for i, s in enumerate(scores) if s > min_score),
            key=lambda x: x[1],
            reverse=True,
        )
        return ranked[:top_k]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def rrf_fuse(rankings: Iterable[Sequence[str]], *, k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion：把多個排序清單合併，分數 = Σ 1 / (k + rank)。"""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


# ---- 切段（結構感知的 recursive chunking）----
#
# 資料是醫院官網爬下來的衛教文章：段落用空行分開、標題是「壹、」「一、」「（一）」「1.」或
# 沒有句尾標點的短行，幾乎沒有 Markdown；表格會被抽成一行一格；網站導覽（「衛教資訊」「聯絡我們」…）
# 常混在內文裡。流程：清行（樣板 / 目錄 / 日期）→ 空行切 block → 辨識 標題 / 條列 / 表格 / 問答
# → 超長 block 依 換行 → 句號 → 逗號 遞迴下探 → 同一小節內貪婪合併到 max_chars、相鄰片段重疊一句
# → 太短的尾巴併回前一段。
# 每個片段前綴「標題路徑」（章 › 節：內文），向量與 prompt 都看得到脈絡。

CHUNK_TARGET_CHARS = 400
CHUNK_HARD_MAX_RATIO = 1.5  # 硬上限 = 目標 × 1.5（600），只在句界收尾
CHUNK_MIN_RATIO = 0.2  # 下限 = 目標 × 0.2（80），更短的併回前一段
TABLE_MIN_ROWS = 6  # 連續這麼多個 ≤ 25 字的短 block 視為表格 / 選單
TABLE_CELL_MAX = 25
HEADING_MAX_CHARS = 30
SHORT_HEADING_MAX_CHARS = 20
OVERLAP_MAX_CHARS = 120
TOC_MIN_LINES = 5  # 連續 5 行以 1–3 位數頁碼結尾視為目錄

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
_ZH_UPPER = re.compile(r"^[壹貳參肆伍陸柒捌玖拾]+[、．.]\s*")
_ZH_LOWER = re.compile(r"^[一二三四五六七八九十]{1,3}[、．.]\s*")
_ZH_PAREN = re.compile(r"^[（(][一二三四五六七八九十\d]{1,3}[)）][、．.]?\s*")
_NUM = re.compile(r"^\d{1,2}(?:[、．)）]|\.(?!\d))\s*")  # 「36.5」不是第 36 項
_BULLET = re.compile(r"^[•●■◎※★◆▲►☞◇○▪\-–—・]\s*")
_QUESTION = re.compile(r"^(Q\s*\d*\s*[:：.．、]|問\s*\d*\s*[:：]|\d{1,2}[.、．)]?\s*Q[:：])", re.I)
_ANSWER = re.compile(r"^(A\s*\d*\s*[:：.．、]|答\s*\d*\s*[:：])", re.I)
_SENTENCE_END = re.compile(r"[。！？!?；;]$")
_ANY_PUNCT_END = re.compile(r"[。！？!?；;，,、：:]$")
_TOC_LINE = re.compile(r"\S\s+\d{1,3}$")
_DATE_LINE = re.compile(r"^\d{2,4}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}\s*日?$")
_NUMERIC_LINE = re.compile(r"^[\d\s.,%/:\-]+$")
# 爬蟲帶進來的頁面殼：QR code、圖片說明、掛號人數、麵包屑、導覽列、下載提示…（語料頻率過濾另外補）
_NOISE_LINE = re.compile(
    r"^(qrcode|圖片說明|圖\d*[:：]?.{0,12}|\(已掛號.*\)|回瀏覽頁|回上頁|回首頁|首頁|列印|列印本頁|分享|下載|"
    r"友善列印|字級[:：]?.*|字體大小.*|點閱[:：]?\s*\d*|瀏覽人次.*|更新日期.*|發布日期.*|張貼日期.*|"
    r"無法觀看.*|更多資訊請點.*|點擊.*|按此.*|請點選.*|>+.*|\[.*\]|::+.*|本院|參考資料|參考文獻|"
    r"延伸閱讀|相關連結|附件|附件下載|檔案下載|top|back|首頁\S{0,24}|\S{0,8}下載\S{0,8})$",
    re.I,
)
_TITLE_NOISE = re.compile(r"[^\w一-鿿]+")
TITLE_SCAN_LINES = 5  # 內文開頭幾行內出現與標題相同的行就刪掉
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])")
_CLAUSE_SPLIT = re.compile(r"(?<=[，,、])")

_LEVEL_UPPER, _LEVEL_LOWER, _LEVEL_PAREN, _LEVEL_NUM, _LEVEL_SHORT = 1, 2, 3, 4, 2


@dataclass
class Chunk:
    """一個檢索片段。

    `content` 已含標題路徑前綴；`heading` 是路徑本身；`kind` = text / list / table / qa。
    """

    content: str
    heading: str = ""
    kind: str = "text"


@dataclass
class _Block:
    text: str
    kind: str = "text"  # text / heading / list / table / qa
    level: int = 0  # heading 用
    marker: str = ""  # 條列 / 標題的編號家族（upper / lower / paren / num / bullet / md / short）


def _line_is_noise(line: str, boilerplate: frozenset[str] | None) -> bool:
    if not line or _NOISE_LINE.match(line) or _DATE_LINE.match(line):
        return True
    return bool(boilerplate and len(line) <= 12 and line in boilerplate)


def clean_lines(
    text: str, *, title: str | None = None, boilerplate: frozenset[str] | None = None
) -> list[str]:
    """逐行清理：去頭尾空白、樣板行、日期 / 純數字行、與標題重複的首行、PDF 目錄（連續頁碼行）。

    回傳的 list 保留空字串當段落邊界（連續空行合併成一個）。
    """
    lines = [ln.strip() for ln in clean_text(text).split("\n")]
    # 與標題重複的行（爬蟲常把 <h1>、麵包屑一起放進內文）：只看開頭幾行、忽略標點與全半形差異
    normalized_title = _TITLE_NOISE.sub("", title or "")
    kept: list[str] = []
    body_lines = 0
    for line in lines:
        if line and normalized_title and body_lines < TITLE_SCAN_LINES:
            body_lines += 1
            if _TITLE_NOISE.sub("", line) == normalized_title:
                continue
        kept.append(line)
    # 目錄：連續 TOC_MIN_LINES 行以頁碼結尾
    out: list[str] = []
    run: list[str] = []
    for line in kept + [""]:
        if line and _TOC_LINE.search(line) and len(line) <= 40:
            run.append(line)
            continue
        if run and len(run) < TOC_MIN_LINES:
            out.extend(run)
        run = []
        out.append(line)
    # 樣板行 → 空行；連續空行合併
    result: list[str] = []
    for line in out:
        if line and _line_is_noise(line, boilerplate):
            line = ""
        if line == "" and (not result or result[-1] == ""):
            continue
        result.append(line)
    while result and result[-1] == "":
        result.pop()
    return result


def _marker(line: str) -> str:
    if _MD_HEADING.match(line):
        return "md"
    if _ZH_UPPER.match(line):
        return "upper"
    if _ZH_LOWER.match(line):
        return "lower"
    if _ZH_PAREN.match(line):
        return "paren"
    if _NUM.match(line):
        return "num"
    if _BULLET.match(line):
        return "bullet"
    return ""


def _strip_marker(line: str) -> str:
    for pattern in (_ZH_UPPER, _ZH_LOWER, _ZH_PAREN, _NUM, _BULLET):
        if pattern.match(line):
            return pattern.sub("", line, count=1).strip()
    match = _MD_HEADING.match(line)
    return match.group(2).strip() if match else line


def _looks_like_heading(line: str) -> bool:
    """單行、不長、沒有句尾標點。編號行是不是標題還要看下一個 block（見 _classify）。"""
    if "\n" in line or len(line) > HEADING_MAX_CHARS or not _CJK.search(line):
        return False
    return not _ANY_PUNCT_END.search(line)


def _classify(blocks: list[str]) -> list[_Block]:
    """把純文字 block 標成 標題 / 條列 / 表格 / 問答 / 內文。"""
    result: list[_Block] = []
    is_short = [len(b) <= TABLE_CELL_MAX and "\n" not in b for b in blocks]
    i = 0
    while i < len(blocks):
        text = blocks[i]
        # 表格 / 選單：連續 TABLE_MIN_ROWS 個短 block（表格被抽成一格一段）
        if is_short[i]:
            j = i
            while (
                j < len(blocks)
                and is_short[j]
                and not _marker(blocks[j])
                and not _QUESTION.match(blocks[j])
            ):
                j += 1
            if j - i >= TABLE_MIN_ROWS:
                cells = blocks[i:j]
                sentences = sum(1 for c in cells if _SENTENCE_END.search(c))
                if all(_NUMERIC_LINE.match(c) for c in cells):
                    pass  # 整串都是數字：分頁「1 2 3 …」，丟掉
                elif sentences * 2 >= len(cells):  # 一句一行的條列，不是表格
                    result.append(_Block("\n".join(cells), kind="list"))
                else:
                    result.append(_Block("｜".join(cells), kind="table"))
                i = j
                continue
        if _NUMERIC_LINE.match(text):
            i += 1  # 落單的數字行（頁碼、掛號人數）
            continue
        marker = _marker(text)
        nxt = blocks[i + 1] if i + 1 < len(blocks) else ""
        if _QUESTION.match(text):
            # 問答：Q 與接著的 A（或下一個 block）綑在一起
            answer = (
                nxt if nxt and not _QUESTION.match(nxt) and not _looks_like_heading(nxt) else ""
            )
            result.append(_Block(f"{text}\n{answer}".strip(), kind="qa"))
            i += 2 if answer else 1
            continue
        if marker == "md" and "\n" not in text:
            level = len(_MD_HEADING.match(text).group(1))
            result.append(_Block(_strip_marker(text), kind="heading", level=level, marker="md"))
            i += 1
            continue
        if marker in ("upper", "lower", "paren", "num") and _looks_like_heading(text):
            # 「一、注意事項」後面接段落 → 標題；「1. 高血壓」「2. 糖尿病」連著出現 → 條列
            if _marker(nxt) != marker:
                level = {
                    "upper": _LEVEL_UPPER,
                    "lower": _LEVEL_LOWER,
                    "paren": _LEVEL_PAREN,
                    "num": _LEVEL_NUM,
                }[marker]
                result.append(
                    _Block(_strip_marker(text), kind="heading", level=level, marker=marker)
                )
                i += 1
                continue
        if marker:
            # 條列：連續同家族的編號 / 符號行合成一個 list block；
            # 前一個以「：」結尾的短句是引言，一起收
            j = i
            while j < len(blocks) and _marker(blocks[j]) == marker:
                j += 1
            lines = blocks[i:j]
            if result and result[-1].kind == "text" and result[-1].text.endswith(("：", ":")):
                lines.insert(0, result.pop().text)
            result.append(_Block("\n".join(lines), kind="list", marker=marker))
            i = j
            continue
        if (
            _looks_like_heading(text)
            and len(text) <= SHORT_HEADING_MAX_CHARS
            and nxt
            and len(nxt) > len(text)
        ):
            result.append(_Block(text, kind="heading", level=_LEVEL_SHORT, marker="short"))
            i += 1
            continue
        result.append(_Block(text))
        i += 1
    return result


def _split_recursive(text: str, limit: int) -> list[str]:
    """超過 limit 的文字依 換行 → 句號 → 逗號 → 字數 逐級下探，直到每段 ≤ limit。"""
    if len(text) <= limit:
        return [text]
    for splitter in (
        lambda s: [p for p in s.split("\n") if p.strip()],
        lambda s: [p for p in _SENTENCE_SPLIT.split(s) if p.strip()],
        lambda s: [p for p in _CLAUSE_SPLIT.split(s) if p.strip()],
    ):
        parts = splitter(text)
        if len(parts) > 1:
            pieces: list[str] = []
            for part in parts:
                pieces.extend(_split_recursive(part.strip(), limit))
            return _pack(pieces, limit)
    return [text[i : i + limit] for i in range(0, len(text), limit)]


def _pack(pieces: list[str], limit: int) -> list[str]:
    """把小片貪婪合併到 ≤ limit（同一 block 內的句子以原樣接續）。"""
    packed: list[str] = []
    current = ""
    for piece in pieces:
        joiner = (
            ""
            if not current or current.endswith(("。", "！", "？", "；", "，", "、", "："))
            else "\n"
        )
        if current and len(current) + len(joiner) + len(piece) > limit:
            packed.append(current)
            current = piece
        else:
            current = f"{current}{joiner}{piece}" if current else piece
    if current:
        packed.append(current)
    return packed


def _last_sentence(text: str) -> str:
    sentences = [s for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    if len(sentences) < 2:
        return ""
    tail = sentences[-1].strip()
    # 只重疊完整的句子；表格列、條列殘句（沒有句尾標點）不帶到下一段
    return tail if len(tail) <= OVERLAP_MAX_CHARS and _SENTENCE_END.search(tail) else ""


def chunk_document(
    text: str,
    *,
    title: str | None = None,
    max_chars: int = CHUNK_TARGET_CHARS,
    hard_max: int | None = None,
    min_chars: int | None = None,
    boilerplate: frozenset[str] | None = None,
) -> list[Chunk]:
    """結構感知 recursive chunking，回傳含標題路徑的片段。

    - `max_chars`：目標大小，同一小節內的段落貪婪合併到這個長度。
    - `hard_max`（預設 1.5 倍）：單一 block 超過就依 換行 / 句號 / 逗號 遞迴切，不在句中斷。
    - `min_chars`（預設 0.2 倍）：更短的片段併回前一段。
    - `title`：文件標題，用來去掉內文開頭重複的標題行；標題本身不進路徑（已另存 title）。
    - `boilerplate`：語料級統計出來的網站導覽行，出現就刪。
    """
    hard_max = hard_max or int(max_chars * CHUNK_HARD_MAX_RATIO)
    min_chars = min_chars if min_chars is not None else int(max_chars * CHUNK_MIN_RATIO)
    lines = clean_lines(text, title=title, boilerplate=boilerplate)
    raw_blocks = [b.strip() for b in "\n".join(lines).split("\n\n") if b.strip()]
    if not raw_blocks:
        return []
    blocks = _classify(raw_blocks)
    # 第一個 block 若是標題，視為文件標題（多半就是 title 的變體），不進路徑
    if blocks and blocks[0].kind == "heading":
        blocks = blocks[1:]

    chunks: list[Chunk] = []
    path: list[tuple[int, str]] = []
    current = ""
    current_heading = ""
    current_kind = "text"

    def flush(*, overlap: bool = False) -> str:
        nonlocal current, current_kind
        carry = ""
        if current:
            chunks.append(Chunk(current, heading=current_heading, kind=current_kind))
            carry = _last_sentence(current) if overlap else ""
        current = ""
        current_kind = "text"
        return carry

    for block in blocks:
        if block.kind == "heading":
            flush()
            path = [(lvl, name) for lvl, name in path if lvl < block.level] + [
                (block.level, block.text)
            ]
            current_heading = " › ".join(name for _, name in path)[:200]
            continue
        for piece in _split_recursive(block.text, max_chars):
            if current and len(current) + 1 + len(piece) > max_chars:
                carry = flush(overlap=True)
                current = (
                    f"{carry}\n{piece}"
                    if carry and len(carry) + 1 + len(piece) <= hard_max
                    else piece
                )
            else:
                current = f"{current}\n{piece}" if current else piece
            if block.kind != "text":
                current_kind = block.kind if current_kind == "text" else current_kind
    flush()

    # 太短的片段併回前一段（放得進硬上限才併）；第一段太短則把第二段併進來
    merged: list[Chunk] = []
    for chunk in chunks:
        if (
            merged
            and len(chunk.content) < min_chars
            and len(merged[-1].content) + len(chunk.content) < hard_max
        ):
            merged[-1] = _merge_chunks(merged[-1], chunk)
        else:
            merged.append(chunk)
    if (
        len(merged) > 1
        and len(merged[0].content) < min_chars
        and len(merged[0].content) + len(merged[1].content) < hard_max
    ):
        merged[:2] = [_merge_chunks(merged[0], merged[1])]

    for chunk in merged:
        if chunk.heading:
            chunk.content = f"{chunk.heading}：{chunk.content}"
    return merged


def _merge_chunks(first: Chunk, second: Chunk) -> Chunk:
    """把 second 併進 first：沿用 first 的標題路徑，second 自己的小節名改成行內前綴，去掉重疊句。"""
    body = second.content
    tail = _last_sentence(first.content)
    if tail and body.startswith(tail):
        body = body[len(tail) :].lstrip("\n")
    if second.heading and second.heading != first.heading:
        body = f"{second.heading}：{body}"
    return Chunk(f"{first.content}\n{body}", heading=first.heading, kind=first.kind)


def chunk_text(text: str, *, max_chars: int = CHUNK_TARGET_CHARS, **kwargs: Any) -> list[str]:
    """`chunk_document()` 的純文字版（只要片段內容）。"""
    return [c.content for c in chunk_document(text, max_chars=max_chars, **kwargs)]


BOILERPLATE_EDGE_LINES = (
    15  # 只統計每篇開頭 / 結尾這麼多行（頁首頁尾導覽），表格表頭在文中不會被算進去
)
_BOILERPLATE_CANDIDATE = re.compile(r"^[\w一-鿿][\w一-鿿 />·・]{1,11}$")


def collect_boilerplate(
    line_iter: Iterable[list[str]], *, min_docs: int = 30, nav_ratio: float = 0.8, max_len: int = 12
) -> frozenset[str]:
    """語料級樣板行統計。

    短行「出現在一連串短行之中」的次數 ≥ min_docs 且占其總出現次數 ≥ nav_ratio → 樣板。

    導覽列是一串短行、在文件頭尾；小節標題（「注意事項」）後面接的是長段落、表格表頭在文中，
    兩者比例都低、會被保留。
    `line_iter` 每個元素是一篇文章的行（已 strip、含空字串）。
    """
    total: Counter[str] = Counter()
    nav: Counter[str] = Counter()
    for lines in line_iter:
        body = [ln for ln in lines if ln]
        seen: set[str] = set()
        for idx, line in enumerate(body):
            if len(line) > max_len or line in seen or not _BOILERPLATE_CANDIDATE.match(line):
                continue
            seen.add(line)
            total[line] += 1
            at_edge = idx < BOILERPLATE_EDGE_LINES or idx >= len(body) - BOILERPLATE_EDGE_LINES
            nxt = body[idx + 1] if idx + 1 < len(body) else ""
            if at_edge and (not nxt or len(nxt) <= TABLE_CELL_MAX):
                nav[line] += 1
    return frozenset(
        line
        for line, count in nav.items()
        if count >= min_docs and count / total[line] >= nav_ratio
    )


def hit_label(hit: dict[str, Any]) -> str:
    """片段的出處標籤：「醫院名《標題》」；沒有醫院就只有標題。"""
    meta = hit.get("meta") or {}
    hospital = meta.get("hospital_name")
    return f"{hospital}《{hit['title']}》" if hospital else hit["title"]


def summarize_hits(hits: list[dict[str, Any]], *, max_chars: int = 3200) -> str:
    """把檢索片段組成 prompt 用的文字：[K1] 出處：內容。"""
    lines: list[str] = []
    total = 0
    for index, hit in enumerate(hits, start=1):
        text = f"[K{index}] {hit_label(hit)}：{hit['content']}"
        if total + len(text) > max_chars:
            break
        lines.append(text)
        total += len(text)
    return "\n".join(lines)
