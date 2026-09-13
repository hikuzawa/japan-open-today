"""収録済みの施設を見直し、対象外になったものと重複を `kagawa.yaml` から外す（ADR 0011）。

数え上げの規則（`tools.collect_spots` の `_skip_reason`）を直したとき、**すでに情報源に
書いてしまったもの**は自動では消えない。S7 後の見直しで、観光協会の一覧に混ざっている
次のものが収録済みだと分かった:

- 美術館の中の作品 1 点（「「鬼屏風」／流政之作（瀬戸大橋記念公園）」）
- 会期のある展示（「香川県立ミュージアム常設展「…」」）
- 運営会社そのもの（「株式会社まんでがん」）
- 運動施設（市民プール・カントリー倶楽部・B&G 海洋センター）

同じ場所が別の項目として二重に入ることもある（「国営讃岐まんのう公園」が観光協会のページと
公式サイトの 2 件）。**公式サイトを一次情報にしている方**を残す。

外すだけで、消したことは分かるようにする（`data/runs/pruned-spots.json` に理由を残す）。

使い方:
    uv run python -m tools.prune_spots            # 外す対象を見るだけ
    uv run python -m tools.prune_spots --apply
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from sitemill.clock import jst_now
from sitemill.settings import Workspace

from japan_open_today.data import load_entries
from tools.collect_spots import _skip_reason

MY_KAGAWA = "my-kagawa.jp"


def _norm_name(name: str) -> str:
    """重複の判定に使う名前。テーマ別の飾り（【…】）と補足（（…））を外す。"""
    name = re.sub(r"【[^】]*】", "", name)
    name = re.sub(r"[（(][^）)]*[）)]", "", name)
    return re.sub(r"\s|・|「|」", "", name)


def _reason_for(entry: dict) -> str | None:
    spot = entry.get("spot") or {}
    if not spot:
        return None
    raw = entry.get("name", "")
    display = (spot.get("names") or {}).get("ja", {}).get("text", "")
    return _skip_reason(raw, raw) or _skip_reason(display, display)


def _duplicates(entries: list[dict]) -> dict[str, str]:
    """同じ場所の 2 件目を選ぶ。残すのは公式サイトを一次情報にしている方。"""
    by_name: dict[str, list[dict]] = {}
    for entry in entries:
        spot = entry.get("spot") or {}
        if not spot:
            continue
        name = (spot.get("names") or {}).get("ja", {}).get("text", "") or entry.get("name", "")
        by_name.setdefault(_norm_name(name), []).append(entry)
    drop: dict[str, str] = {}
    for name, group in by_name.items():
        if len(group) < 2:
            continue
        # 観光協会のページだけを情報源にしているものから外す
        ranked = sorted(group, key=lambda e: MY_KAGAWA in (e.get("official_url") or ""))
        for entry in ranked[1:]:
            drop[entry["id"]] = f"重複（{name} は {ranked[0]['id']} にもある）"
    return drop


def _cut(lines: list[str], source_id: str) -> tuple[list[str], bool]:
    """`- id: <source_id>` のブロックを、直前のコメント行ごと取り除く。"""
    head_re = re.compile(rf"^  - id: {re.escape(source_id)}(\s|#|$)")
    start = next((i for i, ln in enumerate(lines) if head_re.match(ln)), -1)
    if start < 0:
        return lines, False
    end = start + 1
    while end < len(lines) and not lines[end].startswith("  - id:"):
        end += 1
    # 直前の空行とコメント行も一緒に外す
    head = start
    while head > 0 and (lines[head - 1].strip().startswith("#") or not lines[head - 1].strip()):
        head -= 1
    return lines[:head] + lines[end:], True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="kagawa.yaml から外す")
    parser.add_argument("--out", type=Path, default=Path("data/runs/pruned-spots.json"))
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    entries = load_entries(ws)
    drop: dict[str, str] = {}
    for entry in entries:
        reason = _reason_for(entry)
        if reason:
            drop[entry["id"]] = f"対象外（{reason}）"
    drop.update({k: v for k, v in _duplicates(entries).items() if k not in drop})

    by_id = {e["id"]: e for e in entries}
    print(f"== 外す {len(drop)} 件 ==")
    for source_id, reason in sorted(drop.items(), key=lambda kv: kv[1]):
        print(f"  {reason[:28]:30} {source_id:34} {by_id[source_id].get('name', '')[:34]}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "pruned_at": jst_now().isoformat(),
                "dropped": [
                    {"id": k, "name": by_id[k].get("name", ""), "reason": v}
                    for k, v in sorted(drop.items())
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    if not args.apply:
        print("--apply を付けると kagawa.yaml から外す")
        return 0

    path = ws.sources_dir / "kagawa.yaml"
    lines = path.read_text(encoding="utf-8").split("\n")
    removed = 0
    for source_id in drop:
        lines, ok = _cut(lines, source_id)
        removed += 1 if ok else 0
        if not ok:
            print(f"  ! {source_id} が見つからない（すでに外れている）")
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"kagawa.yaml から {removed} 件を外した")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
