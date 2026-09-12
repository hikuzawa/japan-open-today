"""エリア区分（ADR 0003）。香川県を地域と島で切る。

エリア名は**画面の文言ではなくデータ**として持つ（ADR 0005）。地名の訳は公式の英語表記が
あるものに合わせ、無ければローマ字（ヘボン式）にする。用語集と同じ扱いで、変更は差分で見る。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Area:
    slug: str
    ja: str
    en: str
    zh_hant: str
    note_ja: str = ""
    # このエリアに含まれる市町（住所からエリアを決めるために使う）。
    # 香川県の 8 市 9 町をすべてどこかに含める。島は市町より細かく切る（先に判定する）
    municipalities: tuple[str, ...] = ()

    def name(self, locale: str) -> str:
        return {"ja": self.ja, "en": self.en, "zh-Hant": self.zh_hant}.get(locale, self.ja)


AREAS: tuple[Area, ...] = (
    Area(
        "takamatsu",
        "高松",
        "Takamatsu",
        "高松",
        "県庁所在地。港から島へ渡る起点",
        ("高松市",),
    ),
    Area("naoshima", "直島", "Naoshima", "直島", "現代美術の島", ("直島町",)),
    Area("teshima", "豊島", "Teshima", "豐島"),
    Area("megijima", "女木島・男木島", "Megijima and Ogijima", "女木島・男木島"),
    Area("shodoshima", "小豆島", "Shodoshima", "小豆島", "", ("土庄町", "小豆島町")),
    Area("kotohira", "琴平", "Kotohira", "琴平", "", ("琴平町", "まんのう町")),
    Area("marugame", "丸亀", "Marugame", "丸龜", "", ("丸亀市", "多度津町", "善通寺市")),
    Area(
        "sakaide",
        "坂出・宇多津",
        "Sakaide and Utazu",
        "坂出・宇多津",
        "",
        ("坂出市", "宇多津町", "綾川町"),
    ),
    Area("higashikagawa", "東かがわ", "Higashikagawa", "東香川", "", ("東かがわ市",)),
    Area(
        "mitoyo", "三豊・観音寺", "Mitoyo and Kanonji", "三豐・觀音寺", "", ("三豊市", "観音寺市")
    ),
    Area("sanuki", "さぬき", "Sanuki", "讚岐", "", ("さぬき市", "三木町")),
)

BY_SLUG = {a.slug: a for a in AREAS}
# 島は市町の中にあるので、市町より先に住所の地名で判定する
ISLAND_HINTS: tuple[tuple[str, str], ...] = (
    ("豊島", "teshima"),
    ("女木町", "megijima"),
    ("男木町", "megijima"),
    ("直島町", "naoshima"),
)


def area_for_address(address: str) -> str | None:
    """住所からエリアの slug を決める。決められなければ None（推測しない）。

    香川県の 8 市 9 町はすべてどこかのエリアに属する。島（豊島・女木島・男木島・直島）は
    市町より細かいので先に見る。豊島は土庄町にあるため、順番を逆にすると小豆島に入る。
    """
    text = (address or "").replace(" ", "")
    if not text:
        return None
    for hint, slug in ISLAND_HINTS:
        if hint in text:
            return slug
    for candidate in AREAS:
        if any(city in text for city in candidate.municipalities):
            return candidate.slug
    return None


def area(slug: str) -> Area:
    try:
        return BY_SLUG[slug]
    except KeyError as e:
        known = ", ".join(sorted(BY_SLUG))
        raise ValueError(f"未知のエリア: {slug}（使えるのは {known}）") from e
