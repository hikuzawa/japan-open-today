"""香川全域の施設を、公的な一覧から数え上げて型を判定する（S6-3 ①。ADR 0009・0011）。

一次情報は公益社団法人香川県観光協会の「スポット・体験」一覧（my-kagawa.jp）。この一覧は
検索が GET で引けて、各スポットのページが同じ構造（基本情報: 住所・電話・営業時間・定休日・
料金・ウェブサイト）を持つため、**数え上げと型の判定**に使える。

やること:
  1. 一覧をエリアごとに辿って (id, 名前, 詳細 URL) を集める（`--enumerate`）
  2. 詳細ページを取得し、基本情報から型を決める（`--classify`）
     - 営業時間か料金の記載がある → `gated`（ゲートのある施設）
     - どちらも無い → `open_air`（屋外の場所）
     - 対象外（うどん店・直売所・体験・宿。ADR 0011）は `skip` として理由を残す
  3. 公式サイトへのリンクを**辿って**確定する（URL を組み立てない。ADR 0009 追記）

ここは候補を出すだけで、`data/sources/kagawa.yaml` に書くかは別の工程で決める。

使い方:
    uv run python -m tools.collect_spots --enumerate
    uv run python -m tools.collect_spots --classify --limit 40
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from selectolax.parser import HTMLParser
from sitemill.clock import jst_now
from sitemill.diff.normalize import page_text, squash
from sitemill.fetch.client import PoliteClient
from sitemill.settings import Workspace

BASE = "https://www.my-kagawa.jp"
# 検索は GET で引ける。ページ送り（/attraction/digest/list/page:2/...）は 404 を返すため、
# **エリア × 大分類の組み合わせ**で 1 ページに収まる大きさに割って数え上げる
LIST_URL = (
    BASE + "/point/list?sortType={sort}&listStyle=list&area_l%5B%5D={area}"
    "&category_m%5B%5D={category}"
)
# 1 回の検索で返るのは 20 件。並び順を変えると別の 20 件が見えるので、両方を取る
SORTS = ("access", "name")
MAX_PAGES = 12  # 1 ページ 20 件。最大の組み合わせ（73 件）でも 4 ページで足りる
AREAS = {1: "高松市周辺", 2: "香川県東部", 3: "香川県中部", 4: "香川県西部", 5: "島"}
CATEGORIES = (14, 15, 16, 17, 49, 50)  # 検索フォームの大分類
RESULTS = ".searchResult"  # 検索結果のブロック（代表スポットのリンクを混ぜない）

# 第 1 フェーズの対象外（ADR 0011）。理由を残す
SKIP_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("restaurant", re.compile(r"うどん|そば|ラーメン|レストラン|食堂|喫茶|居酒屋|酒蔵|グルメ")),
    # 「ワークショップ」は美術館の催しなので除外語にしない（実測で猪熊弦一郎美術館が落ちた）
    (
        "shop",
        re.compile(r"直売所|物産館|物産市|物産センター|土産|売店|(?<!ワーク)ショップ|マーケット|商店"),
    ),
    (
        "experience",
        re.compile(r"体験|教室|ツアー|レンタサイクル|貸自転車|地曳網|地引網|坐禅|釣り|釣場|釣り堀"),
    ),
    (
        "lodging",
        re.compile(r"ホテル|旅館|民宿|ゲストハウス|コテージ|キャンプ|宿泊|ロッジ|ロッヂ|山荘|の宿"),
    ),
    # 催し・展示。**会期のある出し物**であって、訪ねる場所ではない。
    # 常設展も「その美術館の中の展示」なので、美術館そのものとは別の項目
    (
        "event",
        re.compile(r"まつり|祭り|フェス|花火|イベント|巡回展|企画展|特別展|常設展|共催展|展覧会"),
    ),
    # 美術館の中の作品 1 点。「「ナガレバチ」／流政之作（高松市美術館）」のように
    # 作品名／作者名の形で一覧に入っている。場所ではない
    ("artwork", re.compile(r"／[^／]{2,10}作(?:$|（)|^「[^」]+」／")),
    # 会社そのもの。工房・工場の運営会社が一覧に入っている
    ("company", re.compile(r"株式会社|有限会社|合同会社")),
    # 競技・運動の施設。旅行者の「今日行けるか」の対象ではない（ADR 0011）。
    # 「運動公園」は公園として訪れる場所なので除かない
    (
        "sports",
        re.compile(
            r"ゴルフ|カントリー(?:クラブ|倶楽部)|体育館|球場|テニス|野球|武道館|競技場|弓道|"
            r"市民プール|スポーツランド|運動センター|海洋センター|ウォーキングセンター"
        ),
    ),
    ("shop", re.compile(r"アウトレット|工場直売|ファクトリーショップ")),
    # 高速道路の休憩施設。高速に乗っている人しか寄れないので、行き先にはならない
    # （道の駅は市町・第三セクターの観光施設なので対象に含める）
    ("highway", re.compile(r"パーキングエリア|サービスエリア|ハイウェイオアシス")),
)
# 名前の頭に付く【…】が催し物の名札になっている（「【ものづくり体験】讃岐漆芸美術館」）。
# **「見学」「学習」は入れない**。観光協会は博物館の見学もこの形で載せていて
# （「【施設見学】マルキン醤油記念館」「【産業学習】観音寺市豊浜郷土資料館」）、
# 除くと museum そのものが消える。同じ施設が括弧なしでも載っていれば、重複の判定で 1 件になる。
# 中身が地名なら施設（「【宇多津町】道の駅」）なので、括弧の中の語で見分ける
BRACKET_PROGRAM = re.compile(r"^【[^】]*(?:体験|ツアー|教室|講座)[^】]*】")
# 名前に入っていれば「訪ねる場所」だと分かる語。一覧の分類による除外を覆す（`_skip_reason`）。
# 温泉は入れない（日帰り入浴と旅館の大浴場が名前では見分けられない）。
# 島も入れない（島そのものは別の項目として一覧に出ており、島名を含むキャンプ場が増えるだけ）
KEEP_BY_NAME = re.compile(
    r"美術館|博物館|資料館|記念館|文学館|科学館|民俗館|展示館|郷土館|水族館|動物園|植物園|"
    r"公園|庭園|神社|神宮|大社|八幡|寺|院|城|城跡|城址|古墳|遺跡|史跡|"
    r"展望|灯台|海水浴|海岸|海浜|砂浜|渓谷|渓|滝|峡|山頂|山上|池|ダム|湖|"
    r"道の駅|天文台|プラネタリウム|図書館|文化会館|ホール|ギャラリー|会館"
)
# 複数の場所を並べた名前。区切り記号で見る
COMPOUND = re.compile(r"[・、/／]")
# 基本情報の見出し
INFO_KEYS = ("住所", "電話番号", "営業時間", "定休日", "料金", "アクセス", "駐車場")
FEE_AMOUNT = re.compile(r"\d[\d,]*\s*円|無料")
# 型の判定に使うのは**有料の金額**だけ。「無料」はゲートが無いことの証拠なので、
# これで gated にすると、引田城跡（城山）やフラワーパーク浦島が有料施設になる
PAID_AMOUNT = re.compile(r"\d[\d,]*\s*円")
# 施設の一部（宝物館・駐車場・売店）の時間や料金。**施設そのものの時間ではない**。
# 屋島寺は「宝物館9:30~16:30」だけが載っていて、境内は参拝自由である
_PAREN = re.compile(r"[（(][^）)]*[）)]")
SUB_FACILITY = re.compile(
    r"宝物館|霊宝館|資料館|駐車場|売店|物産|カフェ|レストラン|食堂|喫茶|"
    r"納経|御朱印|ボランティアガイド|貸自転車|ロッカー|プール|温水"
)
CLOCK = re.compile(r"\d{1,2}\s*[:時]\s*\d{0,2}|24\s*時間|終日")
# 宿の印。名前に「ホテル」「旅館」が無い宿がある（「トレスタ白山」）
CHECK_IN = re.compile(r"チェックイン|チェックアウト|IN\s*1[0-9]:|素泊")


@dataclass
class Candidate:
    point_id: str
    name: str
    url: str
    area: str
    spot_type: str = ""  # gated / open_air / skip / unknown
    reason: str = ""
    info: dict[str, str] = field(default_factory=dict)
    official_url: str | None = None
    # ページの「エリア名 分類 施設名」の部分。対象外かの判定に使う。**保存しておく**と、
    # 規則を直したときに数百ページを取得し直さずに決め直せる（`--redecide`）
    head: str = ""


def _entries(html: str, base: str) -> list[tuple[str, str, str]]:
    """検索結果のブロックから (id, 名前, URL)。名前はリンクの文字列（最初の行）。

    ページのどこにでも「代表スポット」へのリンクがあるため、結果のブロックに限る。
    限らないと、どの検索でも同じ 10 件ほどが混ざる。
    """
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    tree = HTMLParser(html)
    block = tree.css_first(RESULTS)
    root = block or tree
    for a in root.css("a"):
        href = a.attributes.get("href") or ""
        m = re.search(r"/point/(\d+)", href)
        if m is None or m.group(1) in seen:
            continue
        text = " ".join((a.text() or "").split())
        if not text:
            continue
        name = text.split(" ")[0]
        seen.add(m.group(1))
        out.append((m.group(1), name, urljoin(base, href.split("#")[0])))
    return out


def _paged(url: str, page: int) -> str:
    """検索の URL に、一覧のページ送りが使う `page:N` を足す。"""
    return url.replace("/point/list?", f"/point/list/page:{page}?", 1)


def enumerate_spots(client: PoliteClient) -> tuple[list[Candidate], list[str]]:
    """エリア × 大分類で数え上げる。返り値は (候補, 取りきれなかった組み合わせ)。

    1 ページは 20 件で、2 ページ目からは検索結果のパスに `page:N` が付く（一覧のページ送りを
    見て確かめた）。**サイト自身が出すページ送りのリンクは 404 を返す**（`/attraction/digest/
    list/page:2/...` に書き換わり、絞り込みの条件も `area_l[0]` の形に変わってしまう）ので、
    こちらは検索が通っているパスに `page:N` だけを足して辿り、結果の中身で確かめる
    （「N 件ありました」が同じで、前のページと違う id が返ること）。
    """
    found: dict[str, Candidate] = {}
    partial: list[str] = []
    for area, area_name in AREAS.items():
        for category in CATEGORIES:
            total = 0
            got: set[str] = set()
            for sort in SORTS:
                base_url = LIST_URL.format(area=area, category=category, sort=sort)
                page = 1
                while True:
                    url = base_url if page == 1 else _paged(base_url, page)
                    r = client.get(url)
                    if not r.ok:
                        if page == 1:
                            print(f"  {area_name}/{category}/{sort}: 取得できない（{r.status}）")
                        break
                    reported = re.search(r"([\d,]+)\s*件ありました", page_text(r.text))
                    total = max(total, int(reported.group(1).replace(",", "")) if reported else 0)
                    fresh = 0
                    for pid, name, url_ in _entries(r.text, BASE):
                        if pid not in got:
                            fresh += 1
                        got.add(pid)
                        if pid not in found:
                            found[pid] = Candidate(
                                point_id=pid, name=name, url=url_, area=area_name
                            )
                    # 新しい id が 1 件も無ければ、そのページ送りは効いていない（同じ結果か空）
                    if fresh == 0 or len(got) >= total or page >= MAX_PAGES:
                        break
                    page += 1
            if total > len(got):
                # 取りきれていないことを記録する（推測で埋めない）
                partial.append(f"{area_name}/大分類{category}: {total} 件のうち {len(got)} 件")
            print(f"  {area_name}/大分類{category}: {total:4} 件 → {len(got):3} 件取得", flush=True)
    return list(found.values()), partial


def _info_table(text: str) -> dict[str, str]:
    """「基本情報 住所 … 電話番号 … 営業時間 …」を項目ごとに切る。"""
    body = text.split("基本情報", 1)[-1].split("周辺観光情報", 1)[0]
    positions: list[tuple[int, str]] = []
    for key in INFO_KEYS:
        i = body.find(key)
        if i >= 0:
            positions.append((i, key))
    positions.sort()
    out: dict[str, str] = {}
    for n, (start, key) in enumerate(positions):
        end = positions[n + 1][0] if n + 1 < len(positions) else len(body)
        out[key] = " ".join(body[start + len(key) : end].split())[:200]
    return out


def page_head(name: str, text: str) -> str:
    """ページの「エリア名 分類 施設名」の部分。対象外かの判定はここだけを見る。

    本文まで見ると落ちる。美術館のページには「体験コーナー」「カフェ」が普通に出てくるので、
    本文に「体験」があるだけで除くと、収録すべき施設が消える。
    """
    at = text.find(name) if name else -1
    return name + " " + (text[:at] if at > 0 else text[:60])


def _skip_reason(name: str, head: str) -> str | None:
    """対象外の型か（ADR 0011）。**名前が決め、分類の見出しは提案するだけ**。

    一覧の分類は観光協会の都合で付いていて、施設の素性とは別のことがある。分類だけで
    除くと、収録すべき施設が消えた（実測。285 件の見直しで見つかった）:

    - 讃岐漆芸美術館・天体望遠鏡博物館・平賀源内記念館 → 分類が「体験」
    - さぬき空港公園・県立亀鶴公園・国営讃岐まんのう公園 → 分類が「キャンプ場」
    - せとしるべ（高松港玉藻防波堤灯台）・やしまーる → 分類が「グルメ」
    - 道の駅 → 分類が「物産・土産」

    そこで、**名前に施設の語（美術館・公園・灯台…）があれば、分類による除外を覆す**。
    名前自身が対象外だと言っているもの（【ものづくり体験】、ホテル、キャンプ場、うどん）は
    そのまま除く。名前に両方あるとき（「一の宮公園・海水浴場・キャンプ場」）は、
    訪ねる場所としての側面があるので残す。
    """
    if BRACKET_PROGRAM.search(name):
        return "experience"  # 名前の頭に「【施設見学】」と書いてある。施設ではなく催し
    out_at = min(
        (m.start() for _, pat in SKIP_RULES if (m := pat.search(name)) is not None),
        default=-1,
    )
    by_name = next((reason for reason, pat in SKIP_RULES if pat.search(name)), None)
    keep_at = KEEP_BY_NAME.search(name)
    if by_name:
        # 施設の語と対象外の語が両方ある名前は、**どちらがその場所の素性か**で決める。
        #   - 施設の語が頭にある（「道の駅「たからだの里さいた」（物産館）」）→ 施設
        #   - 区切り記号で場所を並べている（「一の宮公園・一の宮海岸海水浴場・キャンプ場」）→ 施設
        #   - どちらでもない（「奥の湯公園キャンプ場」「男木島灯台キャンプ場」）→ 末尾の語が素性
        #   - 対象外の語が頭にある（「物産市「道の駅・ことひき」」は道の駅の中の売店。
        #     道の駅そのものは別の項目として一覧にある）→ 対象外
        # 体験・催しは**場所ではなく出し物**なので、どの形でも残さない
        head_first = keep_at is not None and keep_at.start() < out_at
        if head_first and by_name not in ("experience", "event"):
            if keep_at.start() == 0 or COMPOUND.search(name[keep_at.end() : out_at]):
                return None
        return by_name
    if keep_at is not None:
        return None  # 名前が施設だと言っている。分類（見出し）では除かない
    return next((reason for reason, pat in SKIP_RULES if pat.search(head or name)), None)


def classify(client: PoliteClient, cand: Candidate) -> None:
    r = client.get(cand.url)
    if not r.ok:
        cand.spot_type = "unknown"
        cand.reason = f"取得できない（status={r.status}）"
        return
    text = " ".join(page_text(r.text).split())
    if cand.name and squash(cand.name) not in squash(text):
        # 一覧の名前がページに無い。別の施設のページを指している（ADR 0009 追記）。
        # 比較は squash（NFKC＋空白除去）で行う。一覧は全角括弧「屋島（山上）」、ページは
        # 半角括弧「屋島(山上)」で書いていて、そのまま比べると全部外れる
        cand.spot_type = "unknown"
        cand.reason = "ページに一覧の名前が出てこない"
        return
    cand.info = _info_table(text)
    for a in HTMLParser(r.text).css("a"):
        if "公式" in " ".join((a.text() or "").split()):
            href = a.attributes.get("href") or ""
            if href.startswith("http") and "my-kagawa.jp" not in href:
                cand.official_url = href
                break
    cand.head = page_head(cand.name, text)
    skip = _skip_reason(cand.name, cand.head)
    if skip:
        cand.spot_type = "skip"
        cand.reason = skip
        return
    hours = cand.info.get("営業時間", "")
    fee = cand.info.get("料金", "")
    if CHECK_IN.search(hours + " " + fee):
        # 「チェックイン15:00」は宿。名前だけでは分からない（「トレスタ白山」など）
        cand.spot_type = "skip"
        cand.reason = "lodging（チェックイン時刻の記載）"
        return
    if not cand.info:
        # 基本情報の表が無いページ。屋外だからではなく**読めていない**ので unknown にする。
        # 新屋島水族館がこれで、営業時間が無いページから open_air と決めていた
        cand.spot_type = "unknown"
        cand.reason = "基本情報の表が読めない（別の情報源が要る）"
        return
    _decide_gate(cand, hours, fee)


def _own_facility(text: str, name: str = "") -> str:
    """施設の一部（宝物館・駐車場・売店）について書かれた部分を落とす。

    残った文字列に時刻や金額があれば、それは**施設そのもの**のものである。
    落とさないと、宝物館だけ有料の寺が「ゲートのある施設」になり、境内が参拝自由でも
    「不明」と出てしまう（屋島寺・瀬戸大橋の実例）。

    ただし、その施設自身が「ギャラリー」「資料館」であれば、それは一部ではなく本体なので
    落とさない（施設名に同じ語が入っているかで見る）。
    """
    text = _PAREN.sub(
        lambda m: "" if SUB_FACILITY.search(m.group(0)) and m.group(0) not in name else m.group(0),
        text or "",
    )
    kept: list[str] = []
    for part in re.split(r"[、。,\n]|\s{2,}", text):
        part = part.strip()
        if not part:
            continue
        hit = SUB_FACILITY.search(part)
        # 落とすのは「宝物館9:30~16:30」のように**その一部について書かれた**断片だけ。
        # 語が後ろに出てくるだけの断片（「9:00~17:30 ほか売店あり」）は残す
        if hit and hit.start() <= 6 and hit.group(0) not in name:
            continue
        kept.append(part)
    return " ".join(kept)


def _decide_gate(cand: Candidate, hours: str, fee: str) -> None:
    """ゲートのある施設か、屋外の場所か（ADR 0011）。"""
    own_hours = _own_facility(hours, cand.name)
    own_fee = _own_facility(fee, cand.name)
    if CLOCK.search(own_hours) or PAID_AMOUNT.search(own_fee):
        cand.spot_type = "gated"
        cand.reason = f"施設自身の営業時間/有料の記載あり: {(own_hours or own_fee)[:60]}"
        return
    if own_hours != hours or own_fee != fee:
        cand.spot_type = "open_air"
        cand.reason = f"時間・料金は施設の一部のもの（{(hours or fee)[:40]}）"
        return
    cand.spot_type = "open_air"
    cand.reason = "営業時間・有料の記載が無い"


def redecide_from_info(cand: Candidate) -> bool:
    """保存済みの基本情報だけで型を決め直す（取得し直さない）。変わったら True。

    判定の規則を直したあと、数百件を取得し直さずに反映するために使う。`head`（エリア名と
    分類の見出し）を保存してあるものは、対象外かの判定もやり直せる。保存が無い古い記録は
    `gated` と `open_air` だけを見る（`--classify --recheck skip` で取り直せば `head` が付く）。
    """
    if cand.spot_type not in ("gated", "open_air") and not (cand.head and cand.spot_type == "skip"):
        return False
    before = (cand.spot_type, cand.reason)
    by_name = _skip_reason(cand.name, cand.head)
    if by_name:
        cand.spot_type, cand.reason = "skip", by_name
        return (cand.spot_type, cand.reason) != before
    hours = cand.info.get("営業時間", "")
    fee = cand.info.get("料金", "")
    if CHECK_IN.search(hours + " " + fee):
        cand.spot_type, cand.reason = "skip", "lodging（チェックイン時刻の記載）"
    elif not cand.info:
        cand.spot_type, cand.reason = "unknown", "基本情報の表が読めない（別の情報源が要る）"
    else:
        _decide_gate(cand, hours, fee)
    return (cand.spot_type, cand.reason) != before


def _save(out: Path, cands: list[Candidate], requests: int, partial: list[str]) -> dict[str, int]:
    """結果を書き出す。途中でも呼べるようにしてある（数百件の取得をやり直さないため）。"""
    by_type: dict[str, int] = {}
    for cand in cands:
        key = cand.spot_type or "未判定"
        by_type[key] = by_type.get(key, 0) + 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "collected_at": jst_now().isoformat(),
                "requests": requests,
                "by_type": by_type,
                "partial": partial,
                "candidates": [asdict(c) for c in cands],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    return by_type


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enumerate", action="store_true", help="一覧を数え上げる")
    parser.add_argument("--classify", action="store_true", help="詳細ページで型を決める")
    parser.add_argument("--limit", type=int, default=0, help="型の判定を先頭 N 件に絞る")
    parser.add_argument(
        "--recheck", default="", help="この型のものを再判定する（例: skip,unknown）"
    )
    parser.add_argument(
        "--redecide", action="store_true", help="保存済みの基本情報だけで型を決め直す（取得しない）"
    )
    parser.add_argument("--out", type=Path, default=Path("data/runs/kagawa-inventory.json"))
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    stored: dict[str, Any] = {}
    if args.out.is_file():
        stored = json.loads(args.out.read_text(encoding="utf-8"))
    cands = [Candidate(**row) for row in stored.get("candidates", [])]

    if args.redecide:
        changed = sum(1 for cand in cands if redecide_from_info(cand))
        by_type = _save(args.out, cands, stored.get("requests", 0), stored.get("partial", []))
        print(f"== 保存済みの情報から決め直した: {changed} 件が変わった ==")
        for key, n in sorted(by_type.items(), key=lambda kv: -kv[1]):
            print(f"  {key:9} {n:4}")
        return 0

    ua = f"{ws.site.user_agent} spot inventory"
    with PoliteClient(ua, default_delay=3.0, jitter=1.0, timeout=30.0) as client:
        partial: list[str] = list(stored.get("partial", []))
        if args.enumerate or not cands:
            print("== 一覧の数え上げ ==")
            fresh, partial = enumerate_spots(client)
            # 数え上げ直しても**判定済みのものは捨てない**（数百件の取得をやり直さないため）。
            # 新しく見つかった id だけを足す
            known = {c.point_id: c for c in cands}
            added = [c for c in fresh if c.point_id not in known]
            cands = list(known.values()) + added
            print(f"  合計 {len(cands)} 件（うち今回新しく見つかった {len(added)} 件）")
            for line in partial:
                print(f"  ! 取りきれず: {line}")
        if args.classify:
            todo = [c for c in cands if not c.spot_type or c.spot_type in args.recheck.split(",")]
            if args.limit:
                todo = todo[: args.limit]
            print(f"== 型の判定（{len(todo)} 件）==")
            for n, cand in enumerate(todo, 1):
                cand.spot_type = ""
                classify(client, cand)
                head = f"  {n:3}/{len(todo)} {cand.spot_type:9} {cand.name[:22]:24}"
                print(f"{head} {cand.reason[:50]}", flush=True)
                if n % 25 == 0:
                    # 途中で止まっても捨てない（数百件の取得をやり直さないため）
                    _save(args.out, cands, client.request_count, [])
        requests = client.request_count

    by_type = _save(args.out, cands, requests, partial)
    print("\n== 型ごとの件数 ==")
    for key, n in sorted(by_type.items(), key=lambda kv: -kv[1]):
        print(f"  {key:9} {n:4}")
    print(f"（{requests} リクエスト、{args.out}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
