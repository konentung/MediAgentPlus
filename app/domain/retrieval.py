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
        "診所 頭痛 胸痛 發燒 腹瀉 飯前 飯後 睡前 副作用 過敏 出院 傷口 基本資料 真人客服 服藥提醒"
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


def chunk_text(text: str, *, max_chars: int = 400) -> list[str]:
    """依標題 / 空行切段，再把短段落合併到約 max_chars；標題會跟著下一段。"""
    chunks: list[str] = []
    current = ""
    heading = ""
    for raw in re.split(r"\n\s*\n", text.strip()):
        block = raw.strip()
        if not block:
            continue
        if re.match(r"^#{1,6}\s", block) and "\n" not in block:
            heading = block.lstrip("#").strip()
            continue
        piece = f"{heading}：{block}" if heading else block
        heading = ""
        if current and len(current) + len(piece) + 1 > max_chars:
            chunks.append(current)
            current = piece
        else:
            current = f"{current}\n{piece}" if current else piece
        while len(current) > max_chars * 2:
            chunks.append(current[: max_chars * 2])
            current = current[max_chars * 2 :]
    if current:
        chunks.append(current)
    return chunks


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
