"""PDF → 純文字（衛教資訊匯入用，`pypdf`）。

`pypdf` 擷取出來的文字是「版面上的一行一行」，沒有段落資訊，直接切段會把每一頁當成一大塊。
`normalize_pdf_pages()` 把它整理成 `chunk_document()` 吃的格式（段落之間空一行）：
1. 把字型對應表造成的「部首字元」換回一般漢字（PDF 常把「高血壓」抽成康熙部首區的「⾼⾎壓」，
   看起來一樣但字碼不同，不換回來關鍵字檢索會完全對不到）；
2. 去掉頁碼行、每頁重複的頁首 / 頁尾，以及字型沒有文字對應表造成的亂碼行；
3. 把排版折行接回同一段（一行寫滿版面寬度、又不是以句尾標點結束 → 下一行是同一段）；
4. 其餘每一行各自成段，標題、條列項目因此是獨立的 block。
掃描影像檔沒有文字層，擷取結果是空的，這裡直接回錯誤（不做 OCR）。
"""

from __future__ import annotations

import io
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from pypdf import PdfReader

from app.core.exceptions import ValidationError
from app.domain.retrieval import clean_text

_PAGE_NUMBER = re.compile(
    r"^[-–—\s]*(?:第\s*|page\s*)?\d{1,3}(?:\s*頁)?(?:\s*(?:[/／]|of|，?共)\s*\d{1,3}(?:\s*頁)?)?[-–—\s]*$",
    re.I,
)
# 康熙部首（U+2F00–2FDF）、CJK 部首補充（U+2E80–2EFF）、CJK 相容表意文字（U+F900–FAFF：
# 「不 來 力 老 行」這些跟一般漢字長得一樣、字碼不同的字）、拉丁連字（ﬁ ﬂ…，U+FB00–FB06）：
# 只對這幾區做 NFKC 換回一般字元；不能整段 NFKC，全形標點（，：？）會被換成半形
_COMPAT_CHARS = re.compile("[\u2e80-\u2eff\u2f00-\u2fdf\uf900-\ufaff\ufb00-\ufb06]")
_SENTENCE_END = re.compile(r"[。！？!?：:；;]$")
# 條列 / 小節開頭：一、 （一） 1. (1) ● Q： 這類行一定是新的一段，不接在上一行後面
_PARAGRAPH_START = re.compile(
    r"^(?:[一二三四五六七八九十壹貳參肆伍陸柒捌玖拾]+[、.．]|[（(][一二三四五六七八九十\d]+[)）]"
    r"|\d{1,2}[.．、)）]|[●•◆■□▪※＊*\-–]|[QA問答][:：])"
)
# 亂碼行的判斷：PDF 的字型若沒有（或給錯）文字對應表，抽出來的字會是一串不相干文字系統的字元
# （「ꅾ䏞涸麌⹛剤ⵄ倴」「йϞዚଟɰึጩष」），看起來像字、其實沒有意義，留著只會污染檢索。
# 繁中衛教文件預期出現的字母：漢字、拉丁（含越南文的附加符號）、希臘（α β μ）、假名、注音、全形英數
_EXPECTED_LETTER = re.compile(
    "[A-Za-z\u00c0-\u024f\u1e00-\u1eff\u0370-\u03ff\u3040-\u30ff\u3100-\u312f\u4e00-\u9fff\uff00-\uffef]"
)
# 這幾種文字在台灣的文件裡不會是真的內容，單獨出現就算亂碼；其他文字（泰文、韓文…）
# 可能是多語衛教，要同一行混了兩種以上才算
_NEVER_REAL_SCRIPTS = frozenset({"CJK-EXT-A", "YI", "TIFINAGH"})
GARBLED_MIN_LETTERS = 3
GARBLED_RATIO = 0.15  # 非預期文字占這一行字母的比例
# 一頁有這麼多行亂碼 = 這頁的字型壞了：連只有一兩個字、單獨看判斷不出來的行
# （表格的格子、項目符號）只要全是非預期文字也一併拿掉，否則會被併成整段亂碼的片段
GARBLED_PAGE_MIN_LINES = 3
EDGE_LINE_MAX_CHARS = 40  # 頁首 / 頁尾只看這麼短的行
EDGE_REPEAT_RATIO = 0.6  # 出現在這個比例以上的頁面才算頁首 / 頁尾
EDGE_MIN_PAGES = 3
WRAP_WIDTH_RATIO = 0.85  # 行寬達到版面寬度的這個比例視為「寫滿了才折行」


@dataclass(frozen=True)
class PdfText:
    text: str
    pages: int


def extract_pdf_text(data: bytes) -> PdfText:
    """PDF 位元組 → 整理過的純文字；讀不了或沒有文字層回 `ValidationError`。"""
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValidationError("PDF 有密碼保護，無法讀取")
        pages = [page.extract_text() or "" for page in reader.pages]
    except ValidationError:
        raise
    except Exception as exc:  # noqa: BLE001 - pypdf 對壞檔會丟各種例外（PdfReadError、KeyError…）
        raise ValidationError("無法讀取這個 PDF（檔案損毀或格式不支援）") from exc
    text = normalize_pdf_pages(pages)
    if not text:
        raise ValidationError("PDF 內沒有可擷取的文字（掃描影像檔沒有文字層，無法匯入）")
    return PdfText(text=text, pages=len(pages))


def normalize_pdf_pages(pages: list[str]) -> str:
    """各頁擷取出的文字 → 段落之間空一行的純文字。"""
    page_lines = [
        _drop_garbled(
            [
                line
                for raw in _fix_compat_chars(page).split("\n")
                if (line := raw.strip()) and not _PAGE_NUMBER.match(line)
            ]
        )
        for page in pages
    ]
    page_lines = _drop_repeated_edges([lines for lines in page_lines if lines])
    lines = [line for page in page_lines for line in page]
    if not lines:
        return ""
    widths = sorted(_width(line) for line in lines)
    full_width = widths[int(len(widths) * 0.9)] if len(widths) >= 5 else widths[-1]
    paragraphs: list[str] = []
    wrapped = False  # 上一行是寫滿才折行的 → 這一行接在後面
    for line in lines:
        if wrapped and not _PARAGRAPH_START.match(line):
            paragraphs[-1] = _join(paragraphs[-1], line)
        else:
            paragraphs.append(line)
        wrapped = _width(line) >= full_width * WRAP_WIDTH_RATIO and not _SENTENCE_END.search(line)
    return "\n\n".join(paragraphs)


def _fix_compat_chars(text: str) -> str:
    """部首字元換回一般漢字，並去掉控制字元（修改）。

    PDF 抽出的文字偶爾夾著 NUL（0x00）等控制字元，PostgreSQL 的文字欄位不接受 NUL，
    不先去掉整份文件會寫不進資料庫。
    """
    text = clean_text(text)
    return _COMPAT_CHARS.sub(lambda m: unicodedata.normalize("NFKC", m.group()), text)


def _drop_garbled(lines: list[str]) -> list[str]:
    """拿掉一頁裡的亂碼行；亂碼行夠多的頁面連「全是非預期文字的短行」也拿掉。"""
    garbled = [is_garbled_text(line) for line in lines]
    broken_page = sum(garbled) >= GARBLED_PAGE_MIN_LINES
    return [
        line
        for line, bad in zip(lines, garbled, strict=True)
        if not bad and not (broken_page and _only_unexpected_letters(line))
    ]


def _only_unexpected_letters(line: str) -> bool:
    letters = [ch for ch in line if ch.isalpha()]
    return bool(letters) and not any(_EXPECTED_LETTER.match(ch) for ch in letters)


def is_garbled_text(line: str) -> bool:
    """這段文字是不是字型對應錯誤造成的亂碼（見 `_EXPECTED_LETTER` 上方的說明）。

    擷取時逐行檢查；切段後 `KnowledgeService` 再對整個片段檢查一次
    （表格被抽成一格一兩個字，單獨看不出來，併成片段後才看得出整段是亂碼）。
    """
    # 附加符號（泰文、天城文的母音記號…）也算進來：亂碼常常是一串孤立的附加符號
    letters = [ch for ch in line if ch.isalpha() or unicodedata.category(ch).startswith("M")]
    if len(letters) < GARBLED_MIN_LETTERS:
        return False
    odd = [ch for ch in letters if not _EXPECTED_LETTER.match(ch)]
    if len(odd) / len(letters) < GARBLED_RATIO:
        return False
    scripts = {_script(ch) for ch in odd}
    return len(scripts) >= 2 or bool(scripts & _NEVER_REAL_SCRIPTS)


def _script(ch: str) -> str:
    """字元所屬的文字系統（Unicode 名稱的第一個字）；CJK 擴充 A 區另外標出。"""
    if "\u3400" <= ch <= "\u4dbf":
        return "CJK-EXT-A"
    return unicodedata.name(ch, "UNKNOWN").split(" ")[0]


def _drop_repeated_edges(pages: list[list[str]]) -> list[list[str]]:
    """每頁第一行 / 最後一行若在多數頁面重複（院名、文件編號、「第 N 頁」）→ 頁首頁尾，拿掉。"""
    if len(pages) < EDGE_MIN_PAGES:
        return pages
    edges: Counter[str] = Counter()
    for lines in pages:
        edges.update({_edge_key(lines[0]), _edge_key(lines[-1])})
    repeated = {key for key, count in edges.items() if count >= len(pages) * EDGE_REPEAT_RATIO}
    cleaned: list[list[str]] = []
    for lines in pages:
        if lines and _is_edge(lines[0], repeated):
            lines = lines[1:]
        if lines and _is_edge(lines[-1], repeated):
            lines = lines[:-1]
        cleaned.append(lines)
    return cleaned


def _edge_key(line: str) -> str:
    """頁碼會變，數字一律換成 # 再比對。"""
    return re.sub(r"\d+", "#", line)


def _is_edge(line: str, repeated: set[str]) -> bool:
    return len(line) <= EDGE_LINE_MAX_CHARS and _edge_key(line) in repeated


def _width(line: str) -> int:
    """顯示寬度：全形字算 2、其餘算 1。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in line)


def _join(head: str, tail: str) -> str:
    """折行接回：中文之間不加空白，英數之間補一個空白。"""
    if head[-1].isascii() and tail[0].isascii():
        return f"{head} {tail}"
    return head + tail
