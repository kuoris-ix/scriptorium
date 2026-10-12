"""Fictional documents for the tests, to make samples of (`ocr make-samples`) and check with.

Receipts, card statements, invoices and passbook pages are drawn with
made-up shops, items, amounts and dates (every value is random and
fictional), mixing in what OCR misreads: look-alike characters (カ/力,
ロ/口, エ/工, 0/O), ¥ and 円, △ and ▲ for minus, full-width digits and
Japanese era dates. How they're drawn, scanned as variants and written
with their truth is `scriptorium.samples`'s; a program gives its own
documents (`OcrConfig.documents`), as the tests give these.
"""

import math
import random
from collections.abc import Mapping

from scriptorium.samples import Document, FontLoader, Page

# Made-up names, many with look-alike characters.
SHOPS = (
    "架空マート 青葉店",
    "サンプル書房",
    "ゆめのや 本町店",
    "テスト珈琲",
    "ロロ商店",
    "エコの森",
)
ITEMS = (
    "力うどん",
    "カレーパン",
    "ロールケーキ",
    "口どけプリン",
    "エコバッグ",
    "工作キット",
    "ニンジン",
    "二色ボールペン",
    "ペーパータオル",
    "へちまたわし",
    "ソーダ水 500ml",
    "0カロリーゼリー",
    "1L 牛乳",
    "ＩＤカードケース",
    "ハーブティー",
    "ベーグル",
    "一口餃子",
)
CARD_SHOPS = (
    "カフェ・ロロ",
    "エンジニア書房",
    "ニコニコ食堂",
    "サンプル電鉄",
    "テストストア",
    "架空ガス",
    "ゆめタクシー",
    "オンライン工房",
)
PASSBOOK_ENTRIES = (
    "カ)カクウデンキ",
    "フリコミ サンプル",
    "ATM",
    "テスウリヨウ",
    "カ)テストガス",
    "キユウヨ",
    "クレジツト",
    "スイドウ",
)
COMPANIES = ("架空商事株式会社", "サンプル工業株式会社", "株式会社テスト企画")


# Digits, commas and ¥ written full-width.
_WIDE = {ord(c): ord(c) + 0xFEE0 for c in "0123456789,"} | {ord("¥"): 0xFFE5}


def _amount(rng: random.Random, value: int, *, yen: bool | None = None) -> str:
    """`value` written as amounts are, in one of several ways."""
    text = f"{value:,}"
    style = rng.random()
    if yen is True or (yen is None and style < 0.3):
        text = f"¥{text}"
    elif yen is None and style < 0.45:
        text = f"{text}円"
    if rng.random() < 0.1:  # full-width
        text = text.translate(_WIDE)
    return text


def _minus(rng: random.Random, value: int) -> str:
    return rng.choice(("△", "▲", "-")) + f"{value:,}"


def _date(rng: random.Random, year: int, month: int, day: int) -> str:
    era = year - 2018  # 令和
    return rng.choice(
        (
            f"{year}/{month:02d}/{day:02d}",
            f"{year}年{month}月{day}日",
            f"令和{era}年{month}月{day}日",
            f"R{era}.{month}.{day}",
        )
    )


def _day(rng: random.Random) -> tuple[int, int, int]:
    return rng.randint(2030, 2035), rng.randint(1, 12), rng.randint(1, 28)


def receipt(rng: random.Random, dpi: float, font: FontLoader) -> Page:
    items = [(rng.choice(ITEMS), rng.randint(1, 30) * 10 + rng.choice((0, 8))) for _ in range(6)]
    page = Page(80, 75 + 7 * len(items) + 60, dpi, font)
    page.text(40, 6, rng.choice(SHOPS), pt=14, align="center")
    page.text(40, 14, "〒000-0000 架空県サンプル市1-2-3", pt=7, align="center")
    page.text(40, 19, "電話 00-0000-0000", pt=7, align="center", kind="number")
    year, month, day = _day(rng)
    page.text(6, 28, _date(rng, year, month, day), kind="date")
    page.text(74, 28, f"{rng.randint(8, 21)}:{rng.randint(0, 59):02d}", align="right")
    page.text(6, 34, f"レジ{rng.randint(1, 9):04d}  No.{rng.randint(100, 999)}", pt=8)
    y = 44.0
    total = 0
    for name, price in items:
        page.text(6, y, name)
        page.text(74, y, _amount(rng, price, yen=True), align="right", kind="amount")
        total += price
        y += 7
    discount = rng.randint(1, 5) * 10
    page.text(10, y, "値引")
    page.text(74, y, _minus(rng, discount), align="right", kind="amount")
    total -= discount
    y += 9
    page.rule(6, y - 2, 74, y - 2)
    tax = total // 10
    for label, value, pt in (
        ("小計", total, 9.0),
        ("消費税等(10%)", tax, 9.0),
        ("合計", total + tax, 12.0),
    ):
        page.text(6, y, label, pt=pt)
        page.text(74, y, _amount(rng, value, yen=True), pt=pt, align="right", kind="amount")
        y += 7 if pt < 10 else 9
    paid = math.ceil((total + tax) / 1000) * 1000
    page.text(6, y, "お預り")
    page.text(74, y, _amount(rng, paid, yen=True), align="right", kind="amount")
    page.text(6, y + 7, "お釣り")
    page.text(74, y + 7, _amount(rng, paid - total - tax, yen=True), align="right", kind="amount")
    page.text(6, y + 16, "※印は軽減税率(8%)対象商品", pt=6.5)
    return page


def statement(rng: random.Random, dpi: float, font: FontLoader) -> Page:
    page = Page(210, 297, dpi, font)
    page.text(105, 20, "ご利用代金明細書", pt=16, align="center")
    page.text(20, 34, "架空カード株式会社", pt=10)
    year, month, day = _day(rng)
    page.text(20, 44, "お支払日")
    page.text(45, 44, _date(rng, year, month, day), kind="date")
    rows = []
    for _ in range(rng.randint(9, 12)):
        _, m, d = _day(rng)
        value = rng.randint(1, 600) * 10
        refund = rng.random() < 0.15
        rows.append((f"{year}/{m:02d}/{d:02d}", rng.choice(CARD_SHOPS), value, refund))
    total = sum(-v if refund else v for _, _, v, refund in rows)
    page.text(120, 44, "ご請求金額")
    page.text(190, 44, _amount(rng, total, yen=True), pt=12, align="right", kind="amount")
    columns = (20, 50, 125, 160, 190)
    y = 60.0
    page.rule(20, y - 2, 190, y - 2)
    for x, title in zip(columns, ("利用日", "ご利用先", "金額", "備考"), strict=False):
        page.text(x + 2, y, title, pt=8)
    y += 7
    page.rule(20, y - 2, 190, y - 2)
    for when, shop, value, refund in rows:
        page.text(22, y, when, kind="date")
        page.text(52, y, shop)
        amount = _minus(rng, value) if refund else _amount(rng, value, yen=False)
        page.text(158, y, amount, align="right", kind="amount")
        if rng.random() < 0.3:
            page.text(162, y, f"分割{rng.randint(1, 3)}/3", pt=8)
        y += 8
        page.rule(20, y - 2, 190, y - 2)
    page.text(52, y, "合計")
    page.text(158, y, _amount(rng, total, yen=False), align="right", kind="amount")
    for x in columns:
        page.rule(x, 58, x, y - 2)
    page.text(20, y + 14, "※ この明細書は架空のものです。", pt=7)
    return page


def invoice(rng: random.Random, dpi: float, font: FontLoader) -> Page:
    page = Page(210, 297, dpi, font)
    page.text(105, 18, "請求書", pt=20, align="center")
    page.text(20, 36, f"{rng.choice(COMPANIES)} 御中", pt=11)
    year, month, day = _day(rng)
    page.text(140, 36, "請求日")
    page.text(160, 36, _date(rng, year, month, day), kind="date")
    page.text(140, 43, "請求番号")
    page.text(160, 43, f"INV-{rng.randint(0, 9999999):07d}", kind="number")
    company = "架空サービス株式会社"
    page.text(140, 55, company, pt=10)
    page.stamp(185, 57, "架空")
    lines = [(rng.choice(ITEMS), rng.randint(1, 20), rng.randint(1, 500) * 10) for _ in range(4)]
    subtotal = sum(q * p for _, q, p in lines)
    tax = subtotal // 10
    page.text(20, 72, "ご請求金額", pt=12)
    page.text(110, 70, _amount(rng, subtotal + tax, yen=True), pt=16, align="right", kind="amount")
    page.rule(20, 80, 110, 80)
    y = 92.0
    page.text(22, y, "品名", pt=8)
    for x, title in ((118, "数量"), (150, "単価"), (188, "金額")):
        page.text(x, y, title, pt=8, align="right")
    y += 7
    page.rule(20, y - 2, 190, y - 2)
    for name, quantity, price in lines:
        page.text(22, y, name)
        page.text(118, y, str(quantity), align="right", kind="number")
        page.text(150, y, _amount(rng, price, yen=False), align="right", kind="amount")
        page.text(188, y, _amount(rng, quantity * price, yen=False), align="right", kind="amount")
        y += 8
    page.rule(20, y - 2, 190, y - 2)
    for label, value in (("小計", subtotal), ("消費税(10%)", tax), ("合計", subtotal + tax)):
        page.text(125, y, label)
        page.text(188, y, _amount(rng, value, yen=True), align="right", kind="amount")
        y += 8
    page.text(20, y + 10, "お振込先", pt=8)
    page.text(20, y + 16, "架空銀行 サンプル支店 普通", pt=8)
    page.text(68, y + 16, f"{rng.randint(0, 9999999):07d}", pt=8, kind="number")
    return page


def passbook(rng: random.Random, dpi: float, font: FontLoader) -> Page:
    page = Page(170, 120, dpi, font)
    page.text(8, 6, "年月日", pt=8)
    page.text(30, 6, "摘要", pt=8)
    for x, title in ((98, "お支払金額"), (128, "お預り金額"), (162, "差引残高")):
        page.text(x, 6, title, pt=8, align="right")
    page.rule(6, 12, 164, 12)
    balance = rng.randint(100, 2000) * 100
    year, month, day = _day(rng)
    y = 15.0
    for _ in range(12):
        day = min(day + rng.randint(0, 4), 28)
        page.text(8, y, f"{year - 2018:02d}-{month:02d}-{day:02d}", kind="date")
        entry = rng.choice(PASSBOOK_ENTRIES)
        page.text(30, y, entry)
        value = rng.randint(1, 900) * 100 + rng.choice((0, 0, 30, 60))
        incoming = entry in ("フリコミ サンプル", "キユウヨ")
        balance = balance + value if incoming else max(balance - value, 0)
        page.text(
            128 if incoming else 98, y, _amount(rng, value, yen=False), align="right", kind="amount"
        )
        page.text(162, y, f"{balance:,}", align="right", kind="amount")
        y += 8.5
    return page


DOCUMENTS: Mapping[str, Document] = {
    "receipt": receipt,
    "statement": statement,
    "invoice": invoice,
    "passbook": passbook,
}
