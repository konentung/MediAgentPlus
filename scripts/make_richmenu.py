# ruff: noqa: E501  — SVG path 字串本來就長
"""產生 MediAgent+ LINE Rich Menu 圖檔（svg/richmenu-main.svg → svg/richmenu-main.png）。

格線與 app/line/richmenu.py 一致：2500×1686、3 欄 × 2 列、每格 833×843，標籤文字已轉成 path，
產出的 SVG 不依賴系統字型。需要 fontTools 與 Noto Sans TC 變數字型（不納入版控）：

    python3 -m venv /tmp/rm && /tmp/rm/bin/pip install fonttools pillow
    curl -L -o /tmp/NotoSansTC.ttf \
      "https://github.com/google/fonts/raw/main/ofl/notosanstc/NotoSansTC%5Bwght%5D.ttf"
    /tmp/rm/bin/python scripts/make_richmenu.py /tmp/NotoSansTC.ttf svg/richmenu-main.svg

轉 PNG：rsvg-convert / cairosvg 皆可；沒有的話用 headless Chrome：
    chrome --headless=new --force-device-scale-factor=1 --window-size=2500,1686 \
      --screenshot=svg/richmenu-main.png <包 <img src="richmenu-main.svg"> 的 html>
上傳：flask --app wsgi richmenu setup --image svg/richmenu-main.png
"""

import sys

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

FONT, OUT = sys.argv[1], sys.argv[2]
W, H = 2500, 1686
COLS, ROWS = 3, 2
CELL_W, CELL_H = W // COLS, H // ROWS  # 833 × 843，與 app/line/richmenu.py 一致
PAD, RX = 24, 48
CARD_W, CARD_H = CELL_W - 2 * PAD, CELL_H - 2 * PAD
INK, GRAY, PLAT, WHITE, RED, RED_STRONG = (
    "#2B2D42",
    "#8D99AE",
    "#EDF2F4",
    "#FFFFFF",
    "#EF233C",
    "#D90429",
)
STROKE = 15  # 圖示外層再放大 1.25 倍 → 實際約 19px
ICON_SCALE = 1.25
FONT_PX = 96

font = instantiateVariableFont(TTFont(FONT), {"wght": 700}, inplace=False)
cmap, glyphs, hmtx = font.getBestCmap(), font.getGlyphSet(), font["hmtx"]
upem = font["head"].unitsPerEm
scale = FONT_PX / upem


def text_path(text, cx, baseline_y, fill):
    """把文字轉成置中的 <path>。"""
    names = [cmap[ord(ch)] for ch in text]
    total = sum(hmtx[n][0] for n in names) * scale
    x = cx - total / 2
    d = []
    for n in names:
        pen = SVGPathPen(glyphs)
        glyphs[n].draw(TransformPen(pen, (scale, 0, 0, -scale, x, baseline_y)))
        d.append(pen.getCommands())
        x += hmtx[n][0] * scale
    return f'<path d="{" ".join(d)}" fill="{fill}"/>'


def plus(x, y, fill, size=48):
    """小的紅色圓角十字：兩個圓角矩形交叉，圓角約為短邊 30%。"""
    t = size * 0.32
    r = t * 0.3
    return (
        f'<rect x="{x - size / 2:.1f}" y="{y - t / 2:.1f}" width="{size}" height="{t:.1f}" rx="{r:.1f}" fill="{fill}"/>'
        f'<rect x="{x - t / 2:.1f}" y="{y - size / 2:.1f}" width="{t:.1f}" height="{size}" rx="{r:.1f}" fill="{fill}"/>'
    )


# 圖示以 (0,0) 為中心、約 ±120 的框內繪製；stroke 與 fill 透過 currentColor / 參數帶入
def icon_ecg(c, bg):
    # 直接沿用 logo 的 ECG 線（viewBox 0 0 200 200 → 置中放大 2.3 倍）
    return (
        f'<g transform="translate(-193 -240) scale(2.3)"><polyline points="30,104 62,104 78,72 98,138 116,88 126,104 138,104" '
        f'fill="none" stroke="{c}" stroke-width="6.6" stroke-linecap="round" stroke-linejoin="round"/></g>'
    )


def icon_hospital(c, bg):
    return (
        f'<g fill="none" stroke="{c}" stroke-width="{STROKE}" stroke-linecap="round" stroke-linejoin="round">'
        f'<rect x="-104" y="-112" width="208" height="232" rx="14"/>'
        f'<path d="M0,-72 V-8 M-32,-40 H32"/>'
        f'<path d="M-30,120 V56 a30,30 0 0 1 60,0 V120"/>'
        f"</g>"
    )


def icon_door(c, bg):
    return (
        f'<g fill="none" stroke="{c}" stroke-width="{STROKE}" stroke-linecap="round" stroke-linejoin="round">'
        f'<path d="M-30,-120 H-116 V120 H-30"/>'
        f'<path d="M-30,-120 L40,-96 V96 L-30,120 Z"/>'
        f'<path d="M64,0 H136 M104,-34 L138,0 L104,34"/>'
        f'</g><circle cx="16" cy="6" r="10" fill="{c}"/>'
    )


def icon_bottle(c, bg):
    return (
        f'<g fill="none" stroke="{c}" stroke-width="{STROKE}" stroke-linecap="round" stroke-linejoin="round">'
        f'<rect x="-44" y="-126" width="88" height="40" rx="10"/>'
        f'<path d="M-44,-86 V-70 a26,26 0 0 1 -26,26 H-70 a16,16 0 0 0 -16,16 V110 a16,16 0 0 0 16,16 H70 a16,16 0 0 0 16,-16 V-28 a16,16 0 0 0 -16,-16 H70 a26,26 0 0 1 -26,-26 V-86"/>'
        f'<rect x="-52" y="10" width="104" height="72" rx="12"/>'
        f"</g>"
    )


def icon_clock(c, bg):
    return (
        f'<g fill="none" stroke="{c}" stroke-width="{STROKE}" stroke-linecap="round" stroke-linejoin="round">'
        f'<circle cx="-24" cy="-22" r="98"/>'
        f'<path d="M-24,-78 V-22 H22"/>'
        f'<g transform="translate(72 78) rotate(-45)"><rect x="-64" y="-26" width="128" height="52" rx="26" fill="{bg}"/><path d="M0,-26 V26"/></g>'
        f"</g>"
    )


def icon_person(c, bg):
    return (
        f'<g fill="none" stroke="{c}" stroke-width="{STROKE}" stroke-linecap="round" stroke-linejoin="round">'
        f'<circle cx="-34" cy="-40" r="44"/>'
        f'<path d="M-134,120 V104 a100,100 0 0 1 200,0 V120"/>'
        f'<path d="M62,-124 H118 a20,20 0 0 1 20,20 V-54 a20,20 0 0 1 -20,20 H98 L68,-8 V-34 H62 a20,20 0 0 1 -20,-20 V-104 a20,20 0 0 1 20,-20 Z"/>'
        f"</g>"
    )


CELLS = [
    # (標籤, 圖示, 反轉, 十字位置 相對卡片左上)
    ("我不舒服", icon_ecg, True, (CARD_W - 84, 84)),
    ("找醫院", icon_hospital, False, (CARD_W / 2 + 178, 156)),
    ("我剛出院", icon_door, False, (CARD_W - 84, 84)),
    ("去哪拿藥", icon_bottle, False, (CARD_W / 2 + 160, 206)),
    ("服藥提醒", icon_clock, False, (CARD_W - 84, CARD_H - 84)),
    ("找真人", icon_person, False, (84, 84)),
]

ICON_CY = 300  # 圖示中心（相對卡片頂）
TEXT_BASE = 652  # 文字基線（相對卡片頂）

parts = [
    f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
    f'<rect width="{W}" height="{H}" fill="{PLAT}"/>',
]
for i, (label, icon, inverted, (px, py)) in enumerate(CELLS):
    col, row = i % COLS, i // COLS
    x0, y0 = col * CELL_W + PAD, row * CELL_H + PAD
    bg = INK if inverted else WHITE
    fg = PLAT if inverted else INK
    cross = RED if inverted else RED_STRONG
    parts.append(f'<g transform="translate({x0} {y0})" data-cell="{label}">')
    parts.append(f'<rect width="{CARD_W}" height="{CARD_H}" rx="{RX}" fill="{bg}"/>')
    parts.append(
        f'<g transform="translate({CARD_W / 2:.1f} {ICON_CY}) scale({ICON_SCALE})">{icon(fg, bg)}</g>'
    )
    parts.append(text_path(label, CARD_W / 2, TEXT_BASE, fg))
    parts.append(plus(px, py, cross))
    parts.append("</g>")
parts.append("</svg>")
open(OUT, "w", encoding="utf-8").write("\n".join(parts))
print("wrote", OUT, "cell", CELL_W, CELL_H, "card", CARD_W, CARD_H)
