"""営業時間が取れなかった施設に、観光協会のページを営業時間の情報源として足す（S6-3 ①）。

施設の公式サイトを一次情報にできた施設でも、営業時間がそこから取れないことがある
（四国水族館・ゴールドタワーなど、時間が画像や JavaScript の中にある）。一方、観光協会の
一覧のページには基本情報の表があり、**そこには営業時間が載っている**ことを数え上げの時点で
確認している（`data/runs/kagawa-inventory.json` の `info`）。

そこで、次の条件をすべて満たす施設に、観光協会のページを `spot_hours` として足す。

  - `spot_type: gated`（ゲートのある施設）なのに営業時間が取れていない
  - 数え上げで、その施設の観光協会のページに営業時間の記載を確認している
  - その URL をまだ seed していない

観光協会は ADR 0001 が認める情報源である。施設の公式サイトより更新が遅れることはあるので、
**公式サイトから取れているならそちらを使う**（このツールは取れていない施設にだけ足す）。

使い方:
    uv run python -m tools.add_listing_hours          # 何を足すか見るだけ
    uv run python -m tools.add_listing_hours --apply
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from sitemill.settings import Workspace

from japan_open_today.data import Dataset, load_entries

CLOCK = re.compile(r"\d{1,2}\s*[:時]\s*\d{0,2}|24\s*時間|終日")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, default=Path("data/runs/kagawa-inventory.json"))
    parser.add_argument("--apply", action="store_true", help="kagawa.yaml に足す")
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    by_name = {c["name"]: c for c in inventory["candidates"]}
    entries = load_entries(ws)
    seeded = {p["url"] for e in entries for p in (e.get("pages") or [])}
    ds = Dataset.load(ws)

    want: list[tuple[str, str, str]] = []  # (source_id, url, 根拠の抜粋)
    for spot in ds.spots:
        if spot.spot_type != "gated" or spot.hours:
            continue
        cand = by_name.get(spot.name("ja"))
        if cand is None:
            continue
        hours = (cand.get("info") or {}).get("営業時間", "")
        if not CLOCK.search(hours) or cand["url"] in seeded:
            continue
        want.append((spot.source_id, cand["url"], " ".join(hours.split())[:60]))

    print(f"== 観光協会のページを営業時間の情報源に足す: {len(want)} 件 ==")
    for source_id, url, hours in want:
        print(f"  {source_id:28} {url[:44]}  {hours}")
    if not args.apply or not want:
        if not args.apply:
            print("--apply を付けると kagawa.yaml に足す")
        return 0

    path = ws.sources_dir / "kagawa.yaml"
    lines = path.read_text(encoding="utf-8").split("\n")
    by_source: dict[str, list[tuple[str, str]]] = {}
    for source_id, url, hours in want:
        by_source.setdefault(source_id, []).append((url, hours))
    added = 0
    for source_id in sorted(by_source, key=lambda sid: -_source_line(lines, sid)):
        start = _source_line(lines, source_id)
        if start < 0:
            continue
        pages_at = next((i for i in range(start, len(lines)) if lines[i].strip() == "pages:"), None)
        if pages_at is None:
            continue
        end = pages_at + 1
        while end < len(lines) and lines[end].startswith("      "):
            end += 1
        block: list[str] = []
        for url, hours in by_source[source_id]:
            block.append(f"      - url: {url}")
            block.append(f"        kind: spot_hours  # 観光協会の基本情報に営業時間あり: {hours}")
            added += 1
        lines[end:end] = block
        # seed を足したら allow_hosts にもそのホストを入れる。
        # 入れないと「allow_hosts の外へ出ていく」設定になり、検査で止まる
        hosts = {urlparse(url).netloc for url, _ in by_source[source_id]}
        _extend_allow_hosts(lines, start, hosts)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"kagawa.yaml に {added} 件の seed を追加した")
    return 0


def _extend_allow_hosts(lines: list[str], start: int, hosts: set[str]) -> None:
    """その情報源の `allow_hosts` に足りないホストを入れる。"""
    for i in range(start, min(start + 80, len(lines))):
        stripped = lines[i].strip()
        if stripped.startswith("- id: ") and i != start:
            return
        if not stripped.startswith("allow_hosts:"):
            continue
        inside = stripped[stripped.index("[") + 1 : stripped.rindex("]")]
        current = [h.strip() for h in inside.split(",") if h.strip()]
        for host in sorted(hosts):
            if host not in current:
                current.append(host)
        lines[i] = f"    allow_hosts: [{', '.join(current)}]"
        return


def _source_line(lines: list[str], source_id: str) -> int:
    for i, line in enumerate(lines):
        if line.strip().startswith(f"- id: {source_id}") and (
            line.strip()[6:].split("#")[0].strip() == source_id
        ):
            return i
    return -1


if __name__ == "__main__":
    raise SystemExit(main())
