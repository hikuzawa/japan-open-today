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

    def name(self, locale: str) -> str:
        return {"ja": self.ja, "en": self.en, "zh-Hant": self.zh_hant}.get(locale, self.ja)


AREAS: tuple[Area, ...] = (
    Area("takamatsu", "高松", "Takamatsu", "高松", "県庁所在地。港から島へ渡る起点"),
    Area("naoshima", "直島", "Naoshima", "直島", "現代美術の島"),
    Area("teshima", "豊島", "Teshima", "豐島"),
    Area("megijima", "女木島・男木島", "Megijima and Ogijima", "女木島・男木島"),
    Area("shodoshima", "小豆島", "Shodoshima", "小豆島"),
    Area("kotohira", "琴平", "Kotohira", "琴平"),
    Area("marugame", "丸亀", "Marugame", "丸龜"),
    Area("sakaide", "坂出・宇多津", "Sakaide and Utazu", "坂出・宇多津"),
    Area("higashikagawa", "東かがわ", "Higashikagawa", "東香川"),
    Area("mitoyo", "三豊・観音寺", "Mitoyo and Kanonji", "三豐・觀音寺"),
    Area("sanuki", "さぬき", "Sanuki", "讚岐"),
)

BY_SLUG = {a.slug: a for a in AREAS}


def area(slug: str) -> Area:
    try:
        return BY_SLUG[slug]
    except KeyError as e:
        known = ", ".join(sorted(BY_SLUG))
        raise ValueError(f"未知のエリア: {slug}（使えるのは {known}）") from e
