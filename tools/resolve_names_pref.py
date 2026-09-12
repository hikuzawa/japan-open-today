"""県の多言語サイトから、施設名の**公式表記**を取る（ADR 0005 の経路 2）。

香川県観光協会は日本語サイトとは別に、英語（`/en/see-and-do`）と繁体字（`/zh-TW/see-and-do`）の
サイトを持っている。台湾からの直行便がある県なので、**台湾の利用者が実際に検索する表記**は
ここにある。こちらで作った固定訳と食い違うと検索で見つけてもらえない。

両サイトは同じ番号で対応している（`/en/see-and-do/10084` と `/zh-TW/see-and-do/10084` が
どちらも丸亀城）。ページには日本語の漢字で住所が載っているので、**住所で施設を突き合わせる**。
名前の似ている・似ていないでは合わせない（別の施設を掴む）。

使い方:
    uv run python -m tools.resolve_names_pref            # 見るだけ
    uv run python -m tools.resolve_names_pref --apply
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import yaml
from selectolax.parser import HTMLParser
from sitemill.clock import jst_now
from sitemill.fetch.client import PoliteClient
from sitemill.fetch.links import extract_links
from sitemill.settings import Workspace

from japan_open_today.data import Dataset

BASE = "https://www.my-kagawa.jp"
LISTS = {"en": f"{BASE}/en/see-and-do", "zh-Hant": f"{BASE}/zh-TW/see-and-do"}
DETAIL = re.compile(r"/(?:en|zh-TW)/see-and-do/(\d+)")
ADDRESS_LABEL = re.compile(r"(?:地址|Address)\s*")
POSTCODE = re.compile(r"〒?\s*\d{3}-?\d{4}")
NOISE = re.compile(r"[\s　,、。()（）]")


@dataclass
class Entry:
    number: str
    locale: str
    name: str
    address: str
    url: str


def _address_key(address: str) -> str:
    """住所を突き合わせ用の鍵にする。郵便番号・都道府県・記号を落とし、丁目番号を揃える。

    同じ場所でも書き方が違う（「栗林町1丁目20番16号」と「栗林町1-20-16」）。
    揃えないと 1 件も一致しない。
    """
    text = POSTCODE.sub("", address or "")
    text = text.replace("香川県", "").replace("日本", "")
    # 郡は書く側と書かない側がある（「仲多度郡琴平町892-1」と「琴平町892-1」）
    text = re.sub(r"[^\s　]{1,4}郡", "", text)
    text = text.translate(str.maketrans("０１２３４５６７８９－ー‐", "0123456789---"))
    # 数字に付く丁目・番地・号だけを記号に直す。「一番丁」のような地名は壊さない
    text = re.sub(r"(\d)\s*丁目", lambda m: m[1] + "-", text)
    text = re.sub(r"(\d)\s*番地?", lambda m: m[1] + "-", text)
    text = re.sub(r"(\d)\s*号", lambda m: m[1], text)
    text = NOISE.sub("", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:14]


def _same_facility(ja_name: str, other: str) -> bool:
    """日本語名と相手の名前が同じ施設を指しているか、漢字の重なりで見る。

    同じ住所に別の建物があることがある（香川県庁舎の本館と東館）。字体は違う
    （県/縣、亀/龜）ので完全一致は求めず、日本語名の漢字の 6 割が相手にあることを条件にする。
    """
    han = [ch for ch in ja_name if "一" <= ch <= "鿿"]
    if not han:
        return True  # カタカナだけの名前は漢字で確かめられない
    hit = sum(1 for ch in set(han) if ch in other)
    return hit / len(set(han)) >= 0.6


def _detail(html: str, url: str, locale: str, number: str) -> Entry | None:
    tree = HTMLParser(html)
    # h1 は見出しの下にキャッチコピーを抱えている（「丸龜城 日本第一高之石造要塞」）。
    # og:title は名前だけなので、そちらを先に見る
    og = tree.css_first('meta[property="og:title"]')
    name = " ".join((og.attributes.get("content") or "").split()) if og is not None else ""
    if not name:
        heading = tree.css_first("h1")
        first = (heading.text() if heading else "").strip().splitlines()[:1]
        first = first[0] if first else ""
        name = " ".join(first.split())
    body = " ".join((tree.body.text() if tree.body else "").split())
    m = re.search(
        r"(?:地址|Address)\s*([^\s].{0,60}?)(?:\s{2,}|營業時間|Hours|公休日|Closed|費用|Fee)", body
    )
    address = " ".join(m.group(1).split()) if m else ""
    if not name:
        return None
    return Entry(number=number, locale=locale, name=name, address=address, url=url)


def _numbers(client: PoliteClient, list_url: str) -> list[str]:
    """一覧から詳細ページの番号を集める。ページ送りも辿る。"""
    numbers: list[str] = []
    pages = [list_url]
    seen_pages = set()
    while pages:
        url = pages.pop(0)
        if url in seen_pages:
            continue
        seen_pages.add(url)
        res = client.get(url)
        if not res.ok:
            continue
        for link in extract_links(res.text, url):
            m = DETAIL.search(link.url)
            if m and m.group(1) not in numbers:
                numbers.append(m.group(1))
            if re.search(r"[?&]page=\d+", link.url) and link.url not in seen_pages:
                pages.append(urljoin(url, link.url))
    return numbers


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="用語集に書く")
    parser.add_argument(
        "--rematch", action="store_true", help="取得済みの結果で突き合わせだけやり直す"
    )
    parser.add_argument("--out", type=Path, default=Path("data/runs/name-pref-site.json"))
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    ds = Dataset.load(ws)
    by_address: dict[str, list] = {}
    for spot in ds.spots:
        key = _address_key(spot.address.value or spot.address.quote or "")
        if key:
            by_address.setdefault(key, []).append(spot)

    entries: dict[tuple[str, str], Entry] = {}
    requests = 0
    if args.rematch and args.out.is_file():
        stored = json.loads(args.out.read_text(encoding="utf-8"))
        for row in stored.get("entries", []):
            entries[(row["locale"], row["number"])] = Entry(**row)
        print(f"== 取得済みの {len(entries)} 件で突き合わせ直す ==")
    ua = f"{ws.site.user_agent} prefecture site names"
    with PoliteClient(ua, default_delay=2.0, jitter=0.5, timeout=30.0) as client:
        if entries:
            pass
        else:
            for locale, list_url in LISTS.items():
                numbers = _numbers(client, list_url)
                print(f"== {locale}: {len(numbers)} 件の詳細ページ ==")
                for number in numbers:
                    url = f"{list_url}/{number}"
                    res = client.get(url)
                    if not res.ok:
                        continue
                    entry = _detail(res.text, res.final_url, locale, number)
                    if entry is not None:
                        entries[(locale, number)] = entry
        requests = client.request_count

    # 突き合わせは繁体字ページの住所（日本語の漢字で書かれている）で行う。英語ページの住所は
    # ローマ字なので照合できないが、**同じ番号が同じ施設**なので、繁体字で決まった施設に
    # 英語名を当てられる。
    # 住所が**完全に一致**し、しかも 1 対 1 のものだけを採る。
    # 緩めると別の施設を掴む（屋島に「屋島夜景」、鬼ヶ島大洞窟に「女木島」、
    # 琴弾廻廊に「銭形砂絵」が当たった）
    candidates: dict[tuple[str, str], list[Entry]] = {}
    unmatched: list[str] = []
    spot_of_number: dict[str, str] = {}
    for (locale, number), entry in sorted(entries.items()):
        if locale != "zh-Hant":
            continue
        key = _address_key(entry.address)
        spots = by_address.get(key) if key else None
        if not spots and len(key) >= 6:
            # 片方に建物名が付いていることがある（「丸亀市一番丁」と「丸亀市一番丁(丸亀城内)」）。
            # 前方一致も見る。取り違えは漢字の重なりと 1 対 1 の条件で防ぐ
            spots = [
                s
                for k, group in by_address.items()
                if k.startswith(key) or key.startswith(k)
                for s in group
            ]
        if not spots or len(spots) > 1:
            unmatched.append(f"{locale}/{number} {entry.name} / {entry.address}")
            continue
        spot = spots[0]
        if not _same_facility(spot.name("ja"), entry.name):
            # 同じ住所でも別の建物のことがある（県庁舎の本館と東館）。漢字の重なりで確かめる
            unmatched.append(f"{locale}/{number} {entry.name}: {spot.name('ja')} と漢字が合わない")
            continue
        spot_of_number[number] = spot.spot_id
        candidates.setdefault((locale, spot.spot_id), []).append(entry)
    for (locale, number), entry in sorted(entries.items()):
        if locale == "zh-Hant":
            continue
        spot_id = spot_of_number.get(number)
        if spot_id is None:
            unmatched.append(f"{locale}/{number} {entry.name}: 繁体字側で施設が決まっていない")
            continue
        candidates.setdefault((locale, spot_id), []).append(entry)

    matched: list[dict[str, str]] = []
    by_spot = {s.spot_id: s for s in ds.spots}
    for (locale, spot_id), found in sorted(candidates.items()):
        if len(found) > 1:
            names = " / ".join(e.name for e in found)
            unmatched.append(f"{locale} {spot_id}: 候補が複数（{names}）")
            continue
        spot = by_spot[spot_id]
        matched.append(
            {
                "spot_id": spot_id,
                "ja": spot.name("ja"),
                "locale": locale,
                "text": found[0].name,
                "url": found[0].url,
                "address": found[0].address,
            }
        )
        print(f"  {locale:8} {spot.name('ja')[:16]:18} → {found[0].name[:34]}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "resolved_at": jst_now().isoformat(),
                "requests": requests,
                "matched": matched,
                "unmatched": unmatched,
                "entries": [e.__dict__ for e in entries.values()],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    print(f"\n突き合わせ {len(matched)} 件 / 合わない {len(unmatched)} 件 / {requests} リクエスト")
    if args.apply and matched:
        for locale in LISTS:
            _write(ws, locale, [m for m in matched if m["locale"] == locale])
    return 0


def _write(ws: Workspace, locale: str, rows: list[dict[str, str]]) -> None:
    """用語集に公式表記として書く。固定訳（LLM）より優先されるので**上書きする**。"""
    if not rows:
        return
    path = ws.data_dir / "glossary" / f"{locale}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    data = (yaml.safe_load(text) if text else None) or {"names": {}}
    names = data.setdefault("names", {})
    added = replaced = 0
    for row in rows:
        current = names.get(row["ja"])
        if current is not None and current.get("source") == "official":
            continue  # すでに公式表記がある
        if current is not None:
            replaced += 1
        else:
            added += 1
        names[row["ja"]] = {
            "text": row["text"],
            "source": "official",
            "url": row["url"],
            "checked_on": jst_now().date().isoformat(),
            "note": "香川県観光協会の多言語サイトの表記（住所で突き合わせ）",
        }
    header = "\n".join(line for line in text.split("\n") if line.startswith("#"))
    path.write_text(
        (header + "\n" if header else "")
        + yaml.safe_dump(data, allow_unicode=True, sort_keys=True, width=100),
        encoding="utf-8",
        newline="\n",
    )
    print(f"  {path}: 追加 {added} 件 / 固定訳を公式表記に置き換え {replaced} 件")


if __name__ == "__main__":
    raise SystemExit(main())
