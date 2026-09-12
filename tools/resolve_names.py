"""英語・繁体字の施設名を、辿って確定する（ADR 0005）。推測の表記は作らない。

多言語サイトで日本語名のまま出しては読めないので、次の順に自動で解決する。

  1. **施設の公式サイトの英語ページ**（`<link rel=alternate hreflang>`、「English」のリンク、
     `/en/` のようなパス）から、その施設の英語名を取る → `source: official`
  2. **自治体・観光協会の英語ページ**（1 と同じ仕組みで、一次情報にしているページから辿る）
  3. **Wikipedia の言語間リンク**（日本語版の記事 → 英語版・中文版の記事名）。
     Commons のカテゴリを辿ったのと同じやり方で、記事名は推測しない → `source: glossary`
  4. どれも無ければ**入れない**。日本語のまま出す（ADR 0005）

確定したものは `data/glossary/<locale>.yaml` に、由来（`source`）と根拠 URL とともに書く。
凍結して差分でレビューする。

使い方:
    uv run python -m tools.resolve_names --limit 10      # 見るだけ
    uv run python -m tools.resolve_names --apply
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

import yaml
from selectolax.parser import HTMLParser
from sitemill.clock import jst_now
from sitemill.diff.normalize import squash
from sitemill.fetch.client import PoliteClient
from sitemill.settings import Workspace
from sitemill.store.raw import RawCache

from japan_open_today.data import Dataset, load_entries

LOCALES = ("en", "zh-Hant")
# 英語ページへのリンク。ラベルと URL の両方を見る
EN_LINK = re.compile(r"\bEnglish\b|\bEN\b|英語", re.I)
EN_PATH = re.compile(r"/en(?:glish)?(?:/|$)|[?&]lang=en\b|_en\.html?$", re.I)
# 繁体字は**繁体字と分かる印だけ**を見る。「中文」だけのリンクは簡体字のことが多く、
# 実際に四国水族館の中文ページから「四国水族馆」（簡体字）を取ってしまった
ZH_LINK = re.compile(r"繁體|繁体|正體|Traditional", re.I)
ZH_PATH = re.compile(r"/zh[-_](?:tw|hant|hk)(?:/|$)|[?&]lang=zh[-_](?:tw|hant|hk)", re.I)
# 簡体字にしかない字。これが入っていたら繁体字ではない
SIMPLIFIED = re.compile(r"[国馆术园际标园区丰历术产权义习书体乐云价众优会伟传伤]")
# ロケールの印。英語・繁体字ページの URL から外して、元のページと同じものか確かめる
LOCALE_MARK = re.compile(r"/(?:en|english|zh[-_a-z]*|tw|hant)(?=/|$)|[?&]lang=[-a-z]+", re.I)
# 名前として使えない文字列（説明文・サイト名だけ・記号のみ）
BAD_NAME = re.compile(r"[。．\.]{1}\s*\S|^\s*$|^[\W_]+$")
SITE_SUFFIX = re.compile(r"\s*[|｜\-–—:：]\s*.{0,60}$")
# 「Official website」「公式サイト」の後置き
OFFICIAL_TAIL = re.compile(
    r"\s*(?:official\s+(?:web)?site|official|公式(?:ホームページ|サイト|ウェブサイト)?)\s*$", re.I
)
KANA = re.compile(r"[ぁ-んァ-ヶ]")
HAN = re.compile(r"[一-龥]")
LATIN = re.compile(r"[A-Za-z]")
# Wikipedia の API（/w/api.php）は robots.txt で拒否されているので使わない。
# 記事ページ（/wiki/…）は許可されているので、そこから言語間リンクを辿る
WIKI_ARTICLE = "https://ja.wikipedia.org/wiki/"
DISAMBIGUATION = re.compile(r"曖昧さ回避")
# ラテン文字で記事名を書く版。英語版が無いときのローマ字表記として使う
LATIN_WIKIS = ("de", "fr", "es", "it", "pt", "nl", "pl", "id", "vi", "sv")


@dataclass
class Resolved:
    spot_id: str
    ja: str
    locale: str
    text: str
    source: str
    url: str
    note: str = ""


def _name_from_page(html: str, ja_name: str, locale: str) -> tuple[str, str] | None:
    """ページからその施設の名前を取る。返り値は (名前, どこから取ったか)。

    `og:title` → `<h1>` → `<title>` の順。サイト名の後置きは落とす。
    説明文のような長い文字列は名前として採らない。
    """
    tree = HTMLParser(html)
    candidates: list[tuple[str, str]] = []
    for node in tree.css('meta[property="og:title"]'):
        content = (node.attributes.get("content") or "").strip()
        if content:
            candidates.append((content, "og:title"))
    h1 = tree.css_first("h1")
    if h1 is not None:
        candidates.append((" ".join((h1.text() or "").split()), "h1"))
    title = tree.css_first("title")
    if title is not None:
        candidates.append((" ".join((title.text() or "").split()), "title"))
    for raw, where in candidates:
        name = OFFICIAL_TAIL.sub("", SITE_SUFFIX.sub("", raw).strip() or raw.strip()).strip()
        if not name or len(name) > 60 or BAD_NAME.search(name):
            continue
        if squash(name) == squash(ja_name):
            continue  # 日本語名がそのまま出ているだけ
        if not _looks_like(name, locale):
            continue
        return name, where
    return None


def _looks_like(name: str, locale: str) -> bool:
    """その言語の表記に見えるか。

    英語のページから繁体字の表記を取ってしまう事故があった（高松市美術館の繁体字に
    「TAKAMATSU ART MUSEUM」が入った）。字種で弾くのが確実で、推測も入らない。
    """
    if locale == "en":
        # 英語名にかなは出てこない。漢字だけの名前も英語表記ではない
        return bool(LATIN.search(name)) and not KANA.search(name)
    if SIMPLIFIED.search(name):
        return False  # 簡体字は繁体字の表記にしない
    return bool(HAN.search(name)) and not KANA.search(name)


def _alternate_links(html: str, base: str, locale: str) -> list[str]:
    """その言語のページへのリンク。hreflang → ラベル → パスの順に拾う。"""
    tree = HTMLParser(html)
    wanted_lang = "en" if locale == "en" else "zh"
    urls: list[str] = []
    for node in tree.css("link[rel=alternate]"):
        lang = (node.attributes.get("hreflang") or "").lower()
        href = node.attributes.get("href") or ""
        if href and lang.startswith(wanted_lang):
            urls.append(urljoin(base, href))
    label_re, path_re = (EN_LINK, EN_PATH) if locale == "en" else (ZH_LINK, ZH_PATH)
    for a in tree.css("a"):
        href = a.attributes.get("href") or ""
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        label = " ".join((a.text() or "").split())
        if label_re.search(label) or path_re.search(href):
            urls.append(urljoin(base, href.split("#")[0]))
    out: list[str] = []
    for url in urls:
        if url in out or not urlparse(url).netloc:
            continue
        if not _same_page(base, url):
            continue
        out.append(url)
    return out[:3]


def _same_page(source_url: str, candidate: str) -> bool:
    """その言語のページが**元のページに対応している**か。

    「English」のリンクがサイトの英語トップに行くことが多く、そこから名前を取ると
    サイト名を施設名にしてしまう（マルキン醤油記念館に「VISIT KAGAWA」が入った）。
    ロケールの印を外したパスが元のページと一致することを条件にする。
    """
    src = urlparse(source_url)
    cand = urlparse(candidate)
    if (
        src.netloc
        and cand.netloc
        and src.netloc.removeprefix("www.") != cand.netloc.removeprefix("www.")
    ):
        return False
    src_path = LOCALE_MARK.sub("", src.path).strip("/")
    cand_path = LOCALE_MARK.sub("", cand.path).strip("/")
    if src_path == cand_path:
        return True
    # 「/art/chichu.html」→「/en/art/chichu.html」のような入れ替えも許す
    tail = src_path.rsplit("/", 1)[-1]
    return bool(tail) and tail in cand_path


def from_official(
    client: PoliteClient, raw: RawCache, entry: dict, spot, locale: str
) -> Resolved | None:
    """一次情報にしているページから、その言語のページを辿って名前を取る（経路 1・2）。"""
    for page in entry.get("pages") or []:
        html = raw.load_text(entry["id"], page["url"])
        if not html:
            continue
        for url in _alternate_links(html, page["url"], locale):
            res = client.get(url)
            if not res.ok:
                continue
            found = _name_from_page(res.text, spot.name("ja"), locale)
            if found is None:
                continue
            text, where = found
            return Resolved(
                spot_id=spot.spot_id,
                ja=spot.name("ja"),
                locale=locale,
                text=text,
                source="official",
                url=res.final_url,
                note=f"公式サイトの{locale}ページ（{where}）",
            )
    return None


def from_wikipedia(client: PoliteClient, spot) -> dict[str, Resolved]:
    """日本語版の記事から言語間リンクを辿る（経路 3）。記事名は推測しない。

    一覧のテーマ別の前置き（「【施設見学】マルキン醤油記念館」）は外して引く。
    付いたままでは記事が見つからない。API は robots.txt が拒否しているので記事ページを読む。
    """
    ja_name = re.sub(r"^【[^】]*】", "", spot.name("ja")).strip()
    out: dict[str, Resolved] = {}
    if not ja_name:
        return out
    res = client.get(WIKI_ARTICLE + quote(ja_name.replace(" ", "_")))
    if not res.ok:
        return out
    tree = HTMLParser(res.text)
    catlinks = tree.css_first("#catlinks")
    if DISAMBIGUATION.search(" ".join(((catlinks.text() if catlinks else "") or "").split())):
        return out  # 曖昧さ回避のページそのもの。どの記事か決められない
    # 記事がこの施設のものか確かめる（見出しに名前が出てくること）
    heading = tree.css_first("h1")
    title = " ".join((heading.text() if heading else "").split())
    if squash(ja_name) not in squash(title):
        return out
    # **場所の記事**であることも確かめる。名前が一般名詞と同じだと概念の記事に当たる
    # （「【施設見学】株式会社」から「株式会社」の記事を引き、Joint-stock company を
    #   施設の英語名にしてしまった）。座標か香川県の語があることを条件にする
    body = " ".join((tree.body.text() if tree.body else "").split())
    if tree.css_first("#coordinates") is None and "香川" not in body[:4000]:
        return out
    # ラテン文字の版（独語・仏語など）の記事名は、確立したローマ字表記である。英語版が無い
    # 寺社ではこれが「Iyadani-ji」のような通用表記を与える。こちらで作るローマ字ではないので
    # 採ってよい（英語版があればそちらが優先）
    latin: tuple[str, str] | None = None
    for a in tree.css("a.interlanguage-link-target"):
        lang = (a.attributes.get("lang") or a.attributes.get("hreflang") or "").lower()
        href = a.attributes.get("href") or ""
        if not href:
            continue
        text = unquote(href.rstrip("/").rsplit("/", 1)[-1]).replace("_", " ").strip()
        if lang in LATIN_WIKIS and latin is None and len(text) <= 60 and _looks_like(text, "en"):
            latin = (text, lang)
        if lang not in ("en", "zh"):
            continue
        locale = "en" if lang == "en" else "zh-Hant"
        if not text or len(text) > 60 or not _looks_like(text, locale):
            continue
        out[locale] = Resolved(
            spot_id=spot.spot_id,
            ja=spot.name("ja"),
            locale=locale,
            text=text,
            source="glossary",
            url=res.final_url,
            note=f"Wikipedia 日本語版「{title}」の言語間リンク（{lang}）",
        )
    if "en" not in out and latin is not None:
        text, lang = latin
        out["en"] = Resolved(
            spot_id=spot.spot_id,
            ja=spot.name("ja"),
            locale="en",
            text=text,
            source="glossary",
            url=res.final_url,
            note=f"Wikipedia 日本語版「{title}」の言語間リンク（{lang} 版のラテン文字表記）",
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only", default="", help="spot_id をカンマ区切りで絞る")
    parser.add_argument("--apply", action="store_true", help="用語集に書く")
    parser.add_argument("--out", type=Path, default=Path("data/runs/name-resolution.json"))
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    ds = Dataset.load(ws)
    entries = {e["id"]: e for e in load_entries(ws)}
    raw = RawCache(ws.raw_dir)

    todo = [s for s in ds.spots if any(not s.names.get(loc) for loc in LOCALES)]
    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        todo = [s for s in todo if s.spot_id in wanted]
    if args.limit:
        todo = todo[: args.limit]
    print(f"== 訳が無い施設 {len(todo)} 件を解決する ==")

    resolved: list[Resolved] = []
    misses: list[tuple[str, str]] = []
    ua = f"{ws.site.user_agent} name resolution"
    with PoliteClient(ua, default_delay=3.0, jitter=1.0, timeout=30.0) as client:
        for n, spot in enumerate(todo, 1):
            entry = entries.get(spot.source_id) or {}
            got: dict[str, Resolved] = {}
            for locale in LOCALES:
                if spot.names.get(locale):
                    continue
                found = from_official(client, raw, entry, spot, locale)
                if found is not None:
                    got[locale] = found
            if any(not spot.names.get(loc) and loc not in got for loc in LOCALES):
                for locale, found in from_wikipedia(client, spot).items():
                    if locale not in got and not spot.names.get(locale):
                        got[locale] = found
            resolved.extend(got.values())
            for locale in LOCALES:
                if not spot.names.get(locale) and locale not in got:
                    misses.append((spot.spot_id, locale))
            marks = " ".join(f"{loc}={got[loc].text[:22]}" for loc in LOCALES if loc in got)
            print(f"  {n:3}/{len(todo)} {spot.name('ja')[:18]:20} {marks or '（見つからない）'}")
        requests = client.request_count

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "resolved_at": jst_now().isoformat(),
                "requests": requests,
                "resolved": [r.__dict__ for r in resolved],
                "misses": misses,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    by_source: dict[str, int] = {}
    for r in resolved:
        by_source[r.source] = by_source.get(r.source, 0) + 1
    print(
        f"\n確定 {len(resolved)} 件（{by_source}）"
        f" / 見つからない {len(misses)} 件 / {requests} リクエスト"
    )

    if args.apply and resolved:
        for locale in LOCALES:
            rows = [r for r in resolved if r.locale == locale]
            if rows:
                _write_glossary(ws, locale, rows)
    elif args.apply:
        print("書くものが無い")
    return 0


def _write_glossary(ws: Workspace, locale: str, rows: list[Resolved]) -> None:
    """用語集に追記する。既にある訳は上書きしない（凍結。ADR 0005）。"""
    path = ws.data_dir / "glossary" / f"{locale}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None
    data = data or {"names": {}}
    names = data.setdefault("names", {})
    added = 0
    for row in rows:
        if row.ja in names:
            continue  # 凍結。変えるときは差分でレビューする
        names[row.ja] = {
            "text": row.text,
            "source": row.source,
            "url": row.url,
            "checked_on": jst_now().date().isoformat(),
            "note": row.note,
        }
        added += 1
    header = (
        f"# {locale} の固定訳（ADR 0005）。**公式の表記か、辿って確定した表記だけ**を入れる。\n"
        "# 推測のローマ字・訳は作らない。source は由来（official=施設の公式ページ、\n"
        "# glossary=Wikipedia の言語間リンクから確定）、url は根拠。凍結して差分で見る。\n"
        "# 生成: uv run python -m tools.resolve_names --apply\n"
    )
    path.write_text(
        header + yaml.safe_dump(data, allow_unicode=True, sort_keys=True, width=100),
        encoding="utf-8",
        newline="\n",
    )
    print(f"  {path}: {added} 件を追加（合計 {len(names)} 件）")


if __name__ == "__main__":
    raise SystemExit(main())
