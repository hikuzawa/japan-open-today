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

from japan_open_today import klook

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
    # 管理画面の掲載規約を読んで、この設定に写した日。**空のあいだは案件を公開しない**
    # （規約を見ないまま「制約なし」と読んで出さないため。2026-09-22）
    terms_checked_on: str = ""


# Klook の提携 ID（aid）。2026-09-22 承認。計測リンクは www.klook.com の任意の URL に
# `?aid=` を付けた形（運営者が管理画面で確認）。公開の広告リンクに載る値で、秘密ではない
KLOOK_AID = "135769"

ASPS: dict[str, Asp] = {
    # 2026-09-22 承認。**計測されるのは www.klook.com だけ**で、短縮形の s.klook.com は計測されない
    # （運営者が管理画面で確認）。許可ホストを www.klook.com に限り、s.klook.com を検査で落とす。
    # 禁止表現・広告表示の要否は、管理画面の掲載規約を受け取ってから入れる。**それまで案件に
    # 計測 URL を入れない**（Offer.url が空なら「準備中」のまま、何も公開されない）
    "klook": Asp(
        id="klook",
        name="Klook",
        requires_ad_notice=True,
        requires_sponsored_rel=True,
        allowed_link_hosts=("www.klook.com",),
        # 規約に禁止表現の一覧は無い。機械で確かめられる制約は下の 2 つで、どちらも
        # `ad_check` が見る（ADR 0012 追記）
        #  - すべてのリンクに Affiliate ID を付ける（2.4）
        #  - 検索エンジン向けの表示に KLOOK のブランド語を使わない（5.2(a) の SEO / SEM）
        forbidden_phrases=(),
        submit_label="",
        terms_checked_on="2026-09-23",
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
    # 施設ごとに飛び先を変える案件（Klook）。空なら `url` 1 つに送る（従来どおり）
    landings: tuple[klook.Landing, ...] = ()

    @property
    def ready(self) -> bool:
        """公開してよいか。計測 URL・ASP の規約・すべての飛び先が揃ったときだけ。"""
        asp = ASPS.get(self.asp)
        if not self.url or asp is None or not asp.terms_checked_on:
            return False
        return all(landing.complete for landing in self.landings)

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
        name="Klook",
        advertiser="Klook Travel Technology",
        program_id=KLOOK_AID,
        # 素の導線（/go/klook-tickets）の宛先は「香川」の検索結果。その URL を受け取るまで空で、
        # 空のあいだは「準備中」のまま何も公開しない
        url=(
            klook.BY_ID[klook.DEFAULT].url("ja") if klook.BY_ID[klook.DEFAULT].complete else None
        ),
        landing_prefix="https://www.klook.com/",
        placements=("spot-tickets",),
        rank=10,
        approved_on="2026-09-22",
        landings=klook.landings(),
        reward_condition=(
            "成果は 30 日の最終クリック（宿泊は 7 日）。確定は 3 か月後（規約 3.1・3.3）"
        ),
        cookie_days=30,
        # 機械で確かめられないものは文章で残す（ADR 0012）。出典は規約の条項番号
        constraints=(
            "Klook のページを自動で読み取らない（4.3(a) 走査・機械的な抽出の禁止）。"
            "飛び先の生死は人が開いて確かめる",
            "Klook・出品者のブランド語や、Klook での予約に関わる語で検索連動広告・SEO をしない"
            "（5.2(a)）。題名・説明文・見出し・構造化データに Klook の名前を入れない",
            "Klook の画像・ロゴ・説明文は使わない（2.3(e)・4.6。使えるのは Klook が配る"
            "Permitted Promotional Content だけで、いまは受け取っていない）",
            "サイトの見た目を Klook に似せない（4.4(a)）",
            "予約・返金など Klook の取引の問い合わせは support@klook.com へ案内する（2.2(b)）",
            "提携が終わったら Klook へのリンク・記述をすべて外す（7.5）",
            "質の低い送客を続けると停止されうる（4.5(b)）。飛び先は施設に近いものから選ぶ",
        ),
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


@dataclass(frozen=True, eq=False)
class GoTarget:
    """転送ページ 1 枚分。ページ生成と検査が同じ一覧を見る。ロケールごとに 1 枚。

    飛び先を持つ案件（Klook）は、飛び先ごとにも 1 枚ずつ出す
    （`/go/<案件>/<枠>/<飛び先>/`）。どの施設からどの飛び先へ何回押されたかが数えられる。
    """

    offer: Offer
    placement: Placement
    locale: str = "ja"
    prefix: str = ""
    landing: klook.Landing | None = None

    @property
    def url_path(self) -> str:
        path = self.offer.placement_path(self.placement.id, self.prefix)
        return f"{path}{self.landing.id}/" if self.landing else path

    @property
    def rel(self) -> str:
        """ロケールの接頭辞を含まないパス（ページ生成で使う）。"""
        base = f"go/{self.offer.id}/{self.placement.id}/"
        return f"{base}{self.landing.id}/" if self.landing else base

    @property
    def target_url(self) -> str:
        """転送ページが送る先（計測付き）。"""
        if self.landing is not None:
            return self.landing.url(self.locale)
        return self.offer.url or ""


def go_targets(locales: tuple[tuple[str, str], ...] = (("ja", ""),)) -> list[GoTarget]:
    """(ロケール, 接頭辞) の組それぞれに転送ページを 1 枚（飛び先があれば飛び先ごと）。"""
    out: list[GoTarget] = []
    for o in OFFERS:
        if not o.ready:
            continue
        for p in placed(o):
            for code, prefix in locales:
                for landing in o.landings or (None,):
                    out.append(
                        GoTarget(
                            offer=o,
                            placement=PLACEMENT_BY_ID[p],
                            locale=code,
                            prefix=prefix,
                            landing=landing,
                        )
                    )
    return out


def slot_link(offer: Offer, placement: str, locale_prefix: str, spot=None) -> dict:  # noqa: ANN001
    """枠に出すリンク 1 本（転送ページのパスと文言）。飛び先のある案件は施設で選ぶ。"""
    head = f"/{locale_prefix}" if locale_prefix else ""
    if offer.landings and spot is not None:
        landing = klook.landing_for(spot.spot_id, spot.area)
        return {
            "offer": offer,
            "href": f"{head}/go/{offer.id}/{placement}/{landing.id}/",
            "label_key": f"affiliate.{landing.label_key}",
            "landing": landing,
        }
    return {
        "offer": offer,
        "href": offer.placement_path(placement, locale_prefix),
        "label_key": "affiliate.open",
        "landing": None,
    }


def asp_of(offer: Offer) -> Asp | None:
    return ASPS.get(offer.asp)


def link_host(url: str) -> str:
    return urlsplit(url).hostname or ""


def redirects() -> list[Redirect]:
    """/go/<id> → ASP の URL。契約済みのものだけ `_redirects` に出す。

    枠つきの `/go/<id>/<枠>/` は転送ページ（HTML）なのでここには出さない。
    """
    return [Redirect(from_path=o.path, to_url=o.url, status=302) for o in OFFERS if o.ready]
