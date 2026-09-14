"""データスキーマ（ADR 0002）。

数値・時刻・日付・金額は sitemill の FieldValue（引用＋決定的パース）で持つ。

開館時間と定休日は「時刻」ではなく**規則**として持つ（sitemill ADR 0018 の `schedule` モデル）。
今日開いているかを計算するのに規則が必要で、規則が無ければ「不明」と出す。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, Field
from sitemill.models import FieldValue
from sitemill.models.schedule import (
    ClosureRule,
    DaySelector,
    Evidence,
    HoursPeriod,
    SpecialNotice,
)

# 場所の型（ADR 0011）。営業時間が取れるかはゲートの有無で決まるので、充足率を型別に見る
SpotType = Literal["gated", "open_air", "unknown"]
SpotCategory = Literal[
    "museum", "shrine_temple", "park", "garden", "viewpoint", "onsen", "castle", "other"
]
CATEGORY_LABELS_JA = {
    "museum": "美術館・博物館",
    "shrine_temple": "寺社",
    "park": "公園",
    "garden": "庭園",
    "viewpoint": "展望",
    "onsen": "温泉",
    "castle": "城跡",
    "other": "その他",
}
FeeCategory = Literal["adult", "university", "highschool", "junior", "child", "senior", "free"]
FEE_LABELS_JA = {
    "adult": "大人",
    "university": "大学生",
    "highschool": "高校生",
    "junior": "中学生",
    "child": "小学生以下",
    "senior": "65歳以上",
    "free": "無料",
}
TransportMode = Literal["ferry", "rail", "bus"]
ReservationState = Literal["yes", "no", "partial", "unknown"]


class LocalizedName(BaseModel):
    """施設名・路線名。公式が示す表記を優先し、無ければ用語集、無ければ日本語のまま（ADR 0005）。"""

    text: str
    source: Literal["official", "glossary", "ja"] = "ja"
    evidence_url: str | None = None


class Fee(BaseModel):
    """料金 1 件。金額は引用から決定的パーサが作る。"""

    category: FeeCategory
    amount: FieldValue[int] = Field(default_factory=FieldValue)
    note: str | None = None

    @property
    def label_ja(self) -> str:
        return FEE_LABELS_JA[self.category]


class AccessLeg(BaseModel):
    """アクセスの 1 区間。第 2 フェーズの旅程生成で使う。"""

    from_node: str
    mode: TransportMode | Literal["walk", "car", "other"] = "other"
    line: str | None = None
    duration_minutes: FieldValue[int] = Field(default_factory=FieldValue)
    quote: str | None = None


class Spot(BaseModel):
    """観光施設 1 件。事実はすべて引用付き、規則は sitemill の schedule モデル。"""

    spot_id: str
    source_id: str
    area: str
    category: SpotCategory = "other"
    spot_type: SpotType = "unknown"
    names: dict[str, LocalizedName] = Field(default_factory=dict)
    # 共有ページの告知を施設に割り当てるための別表記（「ベネッセハウス」など）。
    # 表示には使わない。公式ページに出る書き方だけを入れる
    aliases: list[str] = Field(default_factory=list)
    official_url: str
    operator: str
    operator_kind: str = "unknown"
    address: FieldValue[str] = Field(default_factory=FieldValue)
    phone: FieldValue[str] = Field(default_factory=FieldValue)
    hours: list[HoursPeriod] = Field(default_factory=list)
    closures: list[ClosureRule] = Field(default_factory=list)
    # 規則が 0 件でも「年中無休」と書かれていれば注記が入る（`always_open`）。
    # 「定休日が無い」と「書かれていない」は別のことなので区別する
    closures_note: str | None = None
    closures_quote: str | None = None
    notices: list[SpecialNotice] = Field(default_factory=list)
    fees: list[Fee] = Field(default_factory=list)
    reservation_required: ReservationState = "unknown"
    reservation_url: str | None = None
    reservation_quote: str | None = None
    access: list[AccessLeg] = Field(default_factory=list)
    visit_minutes: FieldValue[int] = Field(default_factory=FieldValue)
    map_query: str | None = None
    commons_category: str = ""
    social: dict[str, str] = Field(default_factory=dict)
    asset_ids: list[str] = Field(default_factory=list)
    # 鮮度。判定を「不明」に落とすかの判断に使う（ADR 0004）
    hours_fetched_at: str | None = None
    notices_fetched_at: str | None = None
    provenance: dict[str, Any] | None = None
    status: str = "active"
    first_seen_at: str | None = None
    last_seen_at: str | None = None

    def name(self, locale: str) -> str:
        """その言語の表示名。無ければ日本語に落ちる（推測のローマ字は作らない）。"""
        entry = self.names.get(locale) or self.names.get("ja")
        return entry.text if entry else self.spot_id

    @property
    def category_label_ja(self) -> str:
        return CATEGORY_LABELS_JA[self.category]

    @property
    def has_schedule(self) -> bool:
        """開閉を計算する材料があるか。無ければページは「不明」と出す。"""
        return bool(self.hours or self.closures or self.notices)

    @property
    def closes_never(self) -> bool:
        """定休日が無いと原文が言っているか（「年中無休」）。"""
        return not self.closures and self.closures_note == "always_open"

    @property
    def hours_stated(self) -> bool:
        """営業時間が一次情報から取れているか（充足率の分子。ADR 0011）。"""
        return bool(self.hours)

    def path(self, locale_prefix: str = "") -> str:
        head = f"{locale_prefix}/" if locale_prefix else ""
        return f"{head}spots/{self.area}/{self.spot_id}/"


class Route(BaseModel):
    """航路・路線 1 件。第 1 フェーズは始発・終発・便数・所要時間・運賃まで（ADR 0001）。"""

    route_id: str
    source_id: str
    operator_id: str
    names: dict[str, LocalizedName] = Field(default_factory=dict)
    mode: TransportMode
    from_node: str = ""
    to_node: str = ""
    first_departure: FieldValue[str] = Field(default_factory=FieldValue)
    last_departure: FieldValue[str] = Field(default_factory=FieldValue)
    trips_per_day: FieldValue[int] = Field(default_factory=FieldValue)
    duration_minutes: FieldValue[int] = Field(default_factory=FieldValue)
    fares: list[Fee] = Field(default_factory=list)
    service_days: DaySelector | None = None
    service_days_quote: str | None = None
    notices: list[SpecialNotice] = Field(default_factory=list)
    # この航路・路線が結ぶエリア（`areas.py` の slug）。**宣言で持つ**。
    # 着発地の文字列から推測すると、別の島の船を出してしまう（ADR 0009 の原則と同じ）
    areas: list[str] = Field(default_factory=list)
    timetable_url: str | None = None
    # 運賃が PDF・フレーム内にあって値にできない航路のための一次情報リンク（ADR 0001）
    fare_url: str | None = None
    evidence: Evidence | None = None
    notices_fetched_at: str | None = None
    provenance: dict[str, Any] | None = None
    status: str = "active"

    def name(self, locale: str) -> str:
        entry = self.names.get(locale) or self.names.get("ja")
        return entry.text if entry else self.route_id


class TransportOperator(BaseModel):
    """交通事業者。路線をまとめる単位。"""

    operator_id: str
    source_id: str
    names: dict[str, LocalizedName] = Field(default_factory=dict)
    mode: TransportMode
    official_url: str
    notice_url: str | None = None
    operator_kind: str = "transport_operator"

    def name(self, locale: str) -> str:
        entry = self.names.get(locale) or self.names.get("ja")
        return entry.text if entry else self.operator_id


_SLUG_BAD = re.compile(r"[^a-z0-9-]+")


def slugify(text: str) -> str:
    """URL に使える識別子。日本語しか無いときは短いハッシュにする（推測のローマ字は作らない）。"""
    slug = _SLUG_BAD.sub("-", text.strip().lower()).strip("-")
    if slug:
        return slug
    return "s" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def record_id_for(source_id: str, key: str) -> str:
    return hashlib.sha1(f"{source_id}:{key}".encode()).hexdigest()[:16]
