"""収録済みの施設の `spot_type` を、数え上げの判定に合わせ直す（ADR 0011）。

型の判定規則を直したあと（施設の一部の時間・料金を施設のものと見なさない、「無料」を
ゲートの証拠にしないなど）、`data/runs/kagawa-inventory.json` の判定と `kagawa.yaml` の
宣言がずれる。ここで合わせる。

型が変わると**ページの見せ方が変わる**（`gated` は「不明」、`open_air` は「時間の定めは
記載がありません」）。理由を行のコメントに残す。

使い方:
    uv run python -m tools.retype_spots          # 差分を見るだけ
    uv run python -m tools.retype_spots --apply
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sitemill.settings import Workspace

from japan_open_today.data import Dataset, load_entries


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, default=Path("data/runs/kagawa-inventory.json"))
    parser.add_argument("--apply", action="store_true", help="kagawa.yaml を直す")
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    by_name = {c["name"]: c for c in inventory["candidates"]}

    # すでに営業時間・料金を取れている施設は、その**実データ**のほうが一覧より確かな根拠。
    # 一覧に時間が載っていないだけで屋外に落とすと、寒霞渓（ロープウェイの運賃と
    # 季節別の営業時間を取れている）がゲートの無い場所になってしまう
    have_facts = {
        s.spot_id for s in Dataset.load(ws).spots if s.hours or [f for f in s.fees if f.amount.ok]
    }
    changes: list[tuple[str, str, str, str]] = []  # (spot_id, 現在, 新しい型, 理由)
    for entry in load_entries(ws):
        spot = entry.get("spot") or {}
        if not spot:
            continue
        name = (spot.get("names") or {}).get("ja", {}).get("text", "")
        cand = by_name.get(name)
        if cand is None or cand["spot_type"] not in ("gated", "open_air"):
            continue
        current = spot.get("spot_type", "unknown")
        if current == "gated" and spot["spot_id"] in have_facts:
            continue  # 実データで有料・時間ありと分かっている
        if current != cand["spot_type"]:
            changes.append((spot["spot_id"], current, cand["spot_type"], cand["reason"]))

    print(f"== 型を変える施設: {len(changes)} 件 ==")
    for spot_id, before, after, reason in changes:
        print(f"  {spot_id:24} {before:9} → {after:9} {reason[:52]}")
    if not args.apply or not changes:
        if not args.apply:
            print("--apply を付けると kagawa.yaml を直す")
        return 0

    path = ws.sources_dir / "kagawa.yaml"
    lines = path.read_text(encoding="utf-8").split("\n")
    by_spot = {spot_id: (after, reason) for spot_id, _, after, reason in changes}
    fixed = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("spot_id:"):
            continue
        spot_id = stripped.split(":", 1)[1].split("#")[0].strip()
        found = by_spot.get(spot_id)
        if found is None:
            continue
        after, reason = found
        for j in range(i + 1, min(i + 6, len(lines))):
            if lines[j].strip().startswith("spot_type:"):
                lines[j] = f"      spot_type: {after}  # {reason[:70]}"
                fixed += 1
                break
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"kagawa.yaml の {fixed} 件を直した")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
