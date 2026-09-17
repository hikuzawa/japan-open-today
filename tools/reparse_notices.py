"""保存済みの告知を、原文の引用から読み直す（ネットワークにも LLM にも触れない）。

告知の期間は抽出のときに引用から決めて記録に保存している。日付の読み取りを直しても、
相手のページが変わるまで古い値が残る。引用は記録に残っているので、それを読み直して値を
決め直す（quote-then-parse。値は引用からしか作らない）。

直したときに使う:
- 2026-09-17: 和暦の年（「令和7年」「R7年」）と曜日を読むようにした（sitemill v0.7.2）。
  去年の告知を今年の休業にしていた
- 2026-09-17: 別の施設を名指しした告知を取り込まないようにした（`ingest.about_another_place`）

読めなくなった告知（年と曜日が食い違う・日付が無い）と、別の施設の告知は記録から外す。

使い方:
    uv run python -m tools.reparse_notices           # 変わるものを数えるだけ
    uv run python -m tools.reparse_notices --apply   # 記録を書き換える
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sitemill.settings import Workspace

from japan_open_today.data import Dataset
from japan_open_today.ingest import about_another_place
from japan_open_today.spec import parse_notice_span


def reparse(
    notices: list[dict[str, Any]], names: list[str] | None
) -> tuple[list[dict[str, Any]], list[str]]:
    """告知の並びを読み直す。返り値は (残す告知, 変えたことの説明)。"""
    kept: list[dict[str, Any]] = []
    changes: list[str] = []
    for notice in notices:
        quote = (notice.get("evidence") or {}).get("quote") or ""
        label = f"「{notice.get('reason') or ''}」（{quote}）"
        if names is not None:
            other = about_another_place(notice.get("reason") or "", names)
            if other:
                changes.append(f"外す: {label} は別の施設（{other}）の告知")
                continue
        value, why = parse_notice_span(quote)
        if value is None:
            changes.append(f"外す: {label} の期間が読めない（{why}）")
            continue
        start, _, end = value.partition("/")
        span = notice.get("span") or {}
        if span.get("start") != start or span.get("end") != end:
            changes.append(f"直す: {label} {span.get('start')}〜{span.get('end')} → {start}〜{end}")
            notice = {**notice, "span": {"start": start, "end": end}}
        kept.append(notice)
    return kept, changes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="記録を書き換える")
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    ds = Dataset.load(ws)
    names_of = {s.spot_id: [s.name("ja"), *s.aliases] for s in ds.spots}
    total = 0
    for path in sorted((ws.data_dir / "records").glob("*.jsonl")):
        lines = path.read_text(encoding="utf-8").splitlines()
        out: list[str] = []
        changed = False
        for line in lines:
            if not line.strip():
                out.append(line)
                continue
            record = json.loads(line)
            notices = record.get("notices")
            if not notices:
                out.append(line)
                continue
            # 共有のお知らせページ（ADR 0010）は、告知が名指しした施設で割り当て済み。
            # 名前の歯止めは当てない
            shared = path.name.endswith("-shared.jsonl")
            names = (
                None if shared or record.get("route_id") else names_of.get(record.get("spot_id"))
            )
            kept, changes = reparse(notices, names)
            if changes:
                changed = True
                total += len(changes)
                print(f"== {path.name} {record.get('spot_id') or record.get('route_id') or ''}")
                for c in changes:
                    print(f"   {c}")
                record = {**record, "notices": kept}
                out.append(json.dumps(record, ensure_ascii=False, sort_keys=True))
            else:
                out.append(line)
        if changed and args.apply:
            path.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    print(f"\n変更 {total} 件" + ("（書き換えた）" if args.apply else "（--apply で書き換える）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
