"""ASP の案件設定と掲載規約（ADR 0006・0012。akiya-atlas ADR 0010 の移植）。

契約前は「準備中」を出し、ダミーリンクは置かない。ここは「何をどこに出すか」のデータだけを持ち、
実際に出ているかの検査は `ad_check.py` が行う。

akiya-atlas との違いは 2 つ。

1. **枠が 1 ページではなく多数のページに出る**。所有者向けページ 1 枚に置く akiya と違い、
   ここは施設ページ 281 枚・エリアページ 12 枚に同じ枠が出る。枠はページのパスではなく
   **接頭辞**で宣言し、検査はその接頭辞に一致する全ページを見る
2. **3 言語**。転送ページ `/go/<案件>/<枠>/` もロケールごとに出す（英語の利用者に
   日本語の転送ページを見せない）

広告リンクは必ず転送ページを経由する（直リンクを置かない）。転送ページで計測ビーコンが
1 回動くので、どのページのどの枠から何回押されたかが後で分かる。
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from sitemill.models import Redirect

# 種別。1 つの枠に同じ種別を 2 件以上出さない（ADR 0012）
KIND_STAY = "宿泊"
KIND_TICKET = "入場券・体験"
ALL_KINDS = (KIND_STAY, KIND_TICKET)


@dataclass(frozen=True)
class Asp:
    """ASP ごとの掲載規約。ビルド時の検査（`ad_check.py`）はこの値だけを根拠にする。

    規約が変わったらここを直す。ここに書いていない制約は検査されないので、機械で確かめられない
    もの（画像の使い方など）は `Offer.constraints` に文章で残す。

    `allowed_link_hosts` は**空のままにしない**。空の ASP に案件を紐づけて計測 URL を入れると
    検査が止める。提携が承認され、ASP の管理画面で実際のリンクを見てから書く（推測で埋めない）。
    """

    id: str
    name: str
    requires_ad_notice: bool = True
    requires_sponsored_rel: bool = True
    allowed_link_hosts: tuple[str, ...] = ()
    forbidden_phrases: tuple[str, ...] = ()
    submit_label: str = ""


ASPS: dict[str, Asp] = {
    # 提携はこれから（docs/human-tasks.md 11）。許可ホストと禁止表現は、承認後に管理画面の
    # 実物のリンクと規約を見て埋める。埋まるまで案件に計測 URL を入れない
    "klook": Asp(
        id="klook",
        name="Klook",
        requires_ad_notice=True,
        requires_sponsored_rel=True,
        allowed_link_hosts=(),
        forbidden_phrases=(),
        submit_label="",
    ),
    "agoda": Asp(
        id="agoda",
        name="Agoda",
        requires_ad_notice=True,
        requires_sponsored_rel=True,
        allowed_link_hosts=(),
        forbidden_phrases=(),
        submit_label="",
    ),
    "booking": Asp(
        id="booking",
        name="Booking.com",
        requires_ad_notice=True,
        requires_sponsored_rel=True,
        allowed_link_hosts=(),
        forbidden_phrases=(),
        submit_label="",
    ),
}


@dataclass(frozen=True)
class Placement:
    """広告を置ける枠。どのページのどの文脈に、どの種別を置けるか（ADR 0012）。

    `page_prefix` はロケールの接頭辞を**含まない**パス（`spots/`）。検査は各ロケールの
    接頭辞を付けて突き合わせる。
    """

    id: str
    page_prefix: str
    label: str
    kinds: tuple[str, ...]


PLACEMENTS: tuple[Placement, ...] = (
    Placement("spot-tickets", "spots/", "施設ページの「入場券・体験」", (KIND_TICKET,)),
    Placement("area-stay", "areas/", "エリアページの「宿泊」", (KIND_STAY,)),
)
PLACEMENT_BY_ID = {p.id: p for p in PLACEMENTS}


@dataclass(frozen=True)
class Offer:
    """1 案件。契約後に `url` が入るまでは「準備中」表示のまま（ダミーリンクは置かない）。"""

    id: str
    label: str
    kind: str
    description: str
    asp: str = ""
    name: str = ""
    advertiser: str = ""
    program_id: str = ""
    url: str | None = None
    landing_prefix: str = ""
    reward_condition: str = ""
    cookie_days: int | None = None
    constraints: tuple[str, ...] = ()
    placements: tuple[str, ...] = ()
    rank: int = 100
    approved_on: str = ""

    @property
    def ready(self) -> bool:
        return bool(self.url)

    @property
    def path(self) -> str:
        """枠を含まない導線。`_redirects` に出す。"""
        return f"/go/{self.id}"

    def placement_path(self, placement: str, prefix: str = "") -> str:
        """転送ページのパス。`prefix` はロケールの接頭辞（"en" など）。"""
        head = f"/{prefix}" if prefix else ""
        return f"{head}/go/{self.id}/{placement}/"


OFFERS: tuple[Offer, ...] = (
    Offer(
        id="klook-tickets",
        label="入場券・体験",
        kind=KIND_TICKET,
        description="施設の入場券や体験をあらかじめ予約できます。",
        asp="klook",
        placements=("spot-tickets",),
        rank=10,
    ),
    Offer(
        id="agoda-stay",
        label="近くの宿",
        kind=KIND_STAY,
        description="このエリアの宿を探せます。",
        asp="agoda",
        placements=("area-stay",),
        rank=10,
    ),
    Offer(
        id="booking-stay",
        label="近くの宿",
        kind=KIND_STAY,
        description="このエリアの宿を探せます。",
        asp="booking",
        placements=("area-stay",),
        rank=20,
    ),
)


def active_offers() -> list[Offer]:
    return [o for o in OFFERS if o.ready]


def offers_for(placement: str, *, include_pending: bool = False) -> list[Offer]:
    """枠に出す案件。同じ種別は rank の小さい 1 件だけに絞る（ADR 0012）。"""
    if placement not in PLACEMENT_BY_ID:
        raise KeyError(f"未定義の枠: {placement}")
    allowed = PLACEMENT_BY_ID[placement].kinds
    cands = [
        o
        for o in OFFERS
        if placement in o.placements and o.kind in allowed and (o.ready or include_pending)
    ]
    chosen: dict[str, Offer] = {}
    for o in sorted(cands, key=lambda o: (not o.ready, o.rank, o.id)):
        chosen.setdefault(o.kind, o)
    return sorted(chosen.values(), key=lambda o: (ALL_KINDS.index(o.kind), o.rank))


def placed(offer: Offer) -> list[str]:
    """その案件が実際に出る枠（契約前は空）。"""
    return [p for p in offer.placements if offer.ready and p in PLACEMENT_BY_ID]


@dataclass(frozen=True)
class GoTarget:
    """転送ページ 1 枚分。ページ生成と検査が同じ一覧を見る。ロケールごとに 1 枚。"""

    offer: Offer
    placement: Placement
    locale: str = "ja"
    prefix: str = ""

    @property
    def url_path(self) -> str:
        return self.offer.placement_path(self.placement.id, self.prefix)


def go_targets(locales: tuple[tuple[str, str], ...] = (("ja", ""),)) -> list[GoTarget]:
    """(ロケール, 接頭辞) の組それぞれに転送ページを 1 枚。"""
    return [
        GoTarget(offer=o, placement=PLACEMENT_BY_ID[p], locale=code, prefix=prefix)
        for o in OFFERS
        if o.ready
        for p in placed(o)
        for code, prefix in locales
    ]


def asp_of(offer: Offer) -> Asp | None:
    return ASPS.get(offer.asp)


def link_host(url: str) -> str:
    return urlsplit(url).hostname or ""


def redirects() -> list[Redirect]:
    """/go/<id> → ASP の URL。契約済みのものだけ `_redirects` に出す。

    枠つきの `/go/<id>/<枠>/` は転送ページ（HTML）なのでここには出さない。
    """
    return [Redirect(from_path=o.path, to_url=o.url, status=302) for o in OFFERS if o.url]
