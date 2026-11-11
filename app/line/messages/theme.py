"""LINE Flex 的品牌主題：色票與共用元件的單一來源。

色票取自 `svg/` 的 logo 原檔，與後台 `admin/_theme.html` 的 `--ma-*` 變數同一組：
墨藍（主色 / 表頭 / 主要按鈕）、灰藍（次要文字）、白金（淺底 / 分隔線 / 次要按鈕）、
紅（logo 的「+」記號、緊急 / 警示）。各訊息模組一律從這裡取色與元件，不要再硬寫色碼。

Flex 無法內嵌 SVG，所以 logo 的紅色「+」用兩個絕對定位的 box 拼出來（`plus_mark()`），
放在卡片表頭右上角；沒有表頭的卡片則用 `brand_strip()` 這條細的品牌橫條。
"""

from __future__ import annotations

from typing import Any

# 品牌色票（對應 svg：#2b2d42 / #8d99ae / #edf2f4 / #ef233c / #d90429）
INK = "#2B2D42"  # 主色：表頭底、主要按鈕、連結文字
INK_SOFT = "#3A3D59"  # 主色加亮：表頭副標在深底上的對比
GRAY = "#8D99AE"  # 灰藍：深底上的次要文字、淺底上的註腳
MUTED = "#656E83"  # 白底上的次要文字（灰藍加深到符合 WCAG AA，與後台一致）
PLATINUM = "#EDF2F4"  # 白金：淺底、分隔線、次要按鈕底色
RED = "#EF233C"  # 深底 / 白金底上的強調色（logo 的「+」）
RED_STRONG = "#D90429"  # 白底上的強調色：緊急、警示、危險操作
WHITE = "#FFFFFF"

BRAND_NAME = "MediAgent+"
HEADER_PADDING = "16px"
# 表頭右側留給「+」記號的空間，標題換行時不會壓到它
HEADER_PADDING_END = "44px"


def plus_mark(size: int = 14, color: str = RED) -> dict[str, Any]:
    """logo 的「+」：兩個圓角長條交叉（svg 比例：粗 0.4、圓角 0.3）。"""
    arm = max(2, round(size * 0.4))
    offset = f"{(size - arm) // 2}px"
    radius = f"{max(1, round(arm * 0.3))}px"
    bar = {
        "type": "box",
        "layout": "vertical",
        "position": "absolute",
        "backgroundColor": color,
        "cornerRadius": radius,
        "contents": [],
    }
    return {
        "type": "box",
        "layout": "vertical",
        "width": f"{size}px",
        "height": f"{size}px",
        "contents": [
            {
                **bar,
                "width": f"{size}px",
                "height": f"{arm}px",
                "offsetTop": offset,
                "offsetStart": "0px",
            },
            {
                **bar,
                "width": f"{arm}px",
                "height": f"{size}px",
                "offsetTop": "0px",
                "offsetStart": offset,
            },
        ],
    }


def header(title: str, subtitle: str | None = None, *, alert: bool = False) -> dict[str, Any]:
    """卡片表頭：墨藍底 + 白金標題 + 右上角紅色「+」；`alert` 為緊急 / 警示（紅底、白金「+」）。

    `contents[0]` 固定是標題文字，方便測試與 alt text 取用。
    """
    contents: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": title,
            "weight": "bold",
            "size": "md",
            "color": WHITE if alert else PLATINUM,
            "wrap": True,
        }
    ]
    if subtitle:
        contents.append(
            {
                "type": "text",
                "text": subtitle,
                "size": "xs",
                "color": PLATINUM if alert else GRAY,
                "wrap": True,
                "margin": "sm",
            }
        )
    # 「+」記號絕對定位在右上角，位移量對齊 padding
    contents.append(
        {
            **plus_mark(color=PLATINUM if alert else RED),
            "position": "absolute",
            "offsetTop": HEADER_PADDING,
            "offsetEnd": HEADER_PADDING,
        }
    )
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": RED_STRONG if alert else INK,
        "paddingAll": HEADER_PADDING,
        "paddingEnd": HEADER_PADDING_END,
        "contents": contents,
    }


def brand_strip(label: str = BRAND_NAME) -> dict[str, Any]:
    """沒有標題的卡片用這條細橫條當表頭：墨藍底、小「+」+ 品牌名。"""
    return {
        "type": "box",
        "layout": "horizontal",
        "backgroundColor": INK,
        "paddingAll": "10px",
        "paddingStart": HEADER_PADDING,
        "alignItems": "center",
        "spacing": "sm",
        "contents": [
            {**plus_mark(size=10), "flex": 0},
            {
                "type": "text",
                "text": label,
                "size": "xxs",
                "color": GRAY,
                "weight": "bold",
                "flex": 1,
            },
        ],
    }


def bubble(
    body: list[dict[str, Any]],
    *,
    header: dict[str, Any] | None = None,
    footer: list[dict[str, Any]] | None = None,
    size: str | None = None,
) -> dict[str, Any]:
    """組一張 bubble：白底內文、白金分隔線的 footer；`header` 用 `header()` 或 `brand_strip()`。"""
    result: dict[str, Any] = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": HEADER_PADDING,
            "contents": body,
        },
        "styles": {"body": {"backgroundColor": WHITE}},
    }
    if size:
        result["size"] = size
    if header:
        result["header"] = header
    if footer:
        result["footer"] = {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "paddingAll": HEADER_PADDING,
            "contents": footer,
        }
        result["styles"]["footer"] = {"separator": True, "separatorColor": PLATINUM}
    return result


def primary_button(
    action: dict[str, Any], *, alert: bool = False, height: str = "sm"
) -> dict[str, Any]:
    """主要按鈕：墨藍底白字；`alert` 為紅底（撥打 119 等）。"""
    return {
        "type": "button",
        "style": "primary",
        "color": RED_STRONG if alert else INK,
        "height": height,
        "action": action,
    }


def secondary_button(action: dict[str, Any], *, height: str = "sm") -> dict[str, Any]:
    """次要按鈕：白金底、墨藍字。"""
    return {
        "type": "button",
        "style": "secondary",
        "color": PLATINUM,
        "height": height,
        "action": action,
    }


def link_button(action: dict[str, Any], *, height: str = "sm") -> dict[str, Any]:
    """文字連結式按鈕（參考來源、查看全部）。"""
    return {"type": "button", "style": "link", "color": INK, "height": height, "action": action}


def button(
    action: dict[str, Any], *, style: str = "primary", alert: bool = False
) -> dict[str, Any]:
    """依 style 字串選按鈕：primary / secondary / link（給 `button_bubble` 這類泛用入口用）。"""
    if style == "secondary":
        return secondary_button(action)
    if style == "link":
        return link_button(action)
    return primary_button(action, alert=alert)


def text(
    content: str,
    *,
    size: str = "sm",
    bold: bool = False,
    color: str | None = None,
    margin: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """一般文字（自動換行）。"""
    node: dict[str, Any] = {"type": "text", "text": content, "size": size, "wrap": True, **extra}
    if bold:
        node["weight"] = "bold"
    if color:
        node["color"] = color
    if margin:
        node["margin"] = margin
    return node


def secondary_text(content: str, *, size: str = "xs", margin: str | None = None, **extra: Any):
    """次要文字：地址、用法、時間等補充資訊。"""
    return text(content, size=size, color=MUTED, margin=margin, **extra)


def footnote(content: str, *, margin: str = "lg") -> dict[str, Any]:
    """註腳 / 免責聲明：最小字、灰藍。"""
    return text(content, size="xxs", color=GRAY, margin=margin)


def separator(margin: str = "md") -> dict[str, Any]:
    """清單列之間的白金細線。"""
    return {"type": "separator", "color": PLATINUM, "margin": margin}


def row(lines: list[dict[str, Any]], *, first: bool = False) -> list[dict[str, Any]]:
    """清單中的一列：第一列不加分隔線，其餘列前面加一條白金線。"""
    block = {"type": "box", "layout": "vertical", "margin": "md", "contents": lines}
    return [block] if first else [separator(), block]
