"""データの読み込み。`data/sources/*.yaml`（情報源と巡回設定）と `data/records/*.jsonl`（事実）。

1 つの YAML エントリが sitemill の `Source`（巡回設定）と、このサービスの `spot` または
`transport`（表示用の設定）を兼ねる。akiya-atlas と同じ流儀。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from sitemill.diff.freshness import read_at
from sitemill.diff.state import CrawlState
from sitemill.models import Source
from sitemill.settings import Workspace
from sitemill.store.records import RecordStore

from japan_open_today.areas import area
from japan_open_today.schema import LocalizedName, Route, Spot, TransportOperator


def load_entries(ws: Workspace) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(ws.sources_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for entry in data.get("sources", []) or []:
            entry.setdefault("_file", path.name)
            entries.append(entry)
    ids = [e.get("id") for e in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("sources の id が重複している")
    return entries


def load_sources(ws: Workspace) -> list[Source]:
    return [Source.model_validate(e) for e in load_entries(ws)]


def load_glossary(ws: Workspace) -> dict[str, dict[str, LocalizedName]]:
    """`data/glossary/<locale>.yaml` の固定訳（ADR 0005）。日本語名から引く。

    入れてよいのは**公式の表記か、辿って確定した表記**だけ。推測のローマ字は作らない。
    由来は `source`（official / glossary）と `url` で区別し、凍結して差分でレビューする。
    """
    out: dict[str, dict[str, LocalizedName]] = {}
    directory = ws.data_dir / "glossary"
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        names: dict[str, LocalizedName] = {}
        for ja_name, row in (data.get("names") or {}).items():
            if not isinstance(row, dict) or not (row.get("text") or "").strip():
                continue
            names[ja_name] = LocalizedName(
                text=row["text"].strip(),
                source=row.get("source", "glossary"),
                evidence_url=row.get("url"),
            )
        out[path.stem] = names
    return out


def spot_from_entry(entry: dict[str, Any]) -> Spot | None:
    """YAML の spot ブロックから、まだ事実の入っていない Spot を作る。"""
    block = entry.get("spot")
    if not block:
        return None
    payload = {
        "source_id": entry["id"],
        "official_url": entry["official_url"],
        "operator": entry["operator"],
        "operator_kind": entry.get("operator_kind", "unknown"),
        **block,
    }
    payload.setdefault("spot_id", entry["id"])
    try:
        spot = Spot.model_validate(payload)
    except ValidationError as e:
        raise ValueError(f"{entry['id']} の spot 設定が不正: {e}") from e
    area(spot.area)  # 未知のエリアはここで落とす
    return spot


def operator_from_entry(entry: dict[str, Any]) -> TransportOperator | None:
    block = entry.get("transport")
    if not block:
        return None
    payload = {
        "source_id": entry["id"],
        "official_url": entry["official_url"],
        "operator_id": block.get("operator_id", entry["id"]),
        "mode": block["mode"],
        "names": block.get("names", {}),
        "notice_url": block.get("notice_url"),
    }
    try:
        return TransportOperator.model_validate(payload)
    except ValidationError as e:
        raise ValueError(f"{entry['id']} の transport 設定が不正: {e}") from e


def routes_from_entry(entry: dict[str, Any]) -> list[Route]:
    """YAML に書いた航路・路線の枠（事実は抽出で埋める）。"""
    block = entry.get("transport") or {}
    out: list[Route] = []
    for row in block.get("routes", []) or []:
        payload = {
            "source_id": entry["id"],
            "operator_id": block.get("operator_id", entry["id"]),
            "mode": block["mode"],
            **row,
        }
        try:
            out.append(Route.model_validate(payload))
        except ValidationError as e:
            raise ValueError(f"{entry['id']} の routes 設定が不正: {e}") from e
    return out


def _fill_names(payload: dict[str, Any], glossary: dict[str, dict[str, LocalizedName]]) -> None:
    """その言語の表記が無い施設に、用語集の固定訳を当てる（ADR 0005）。

    当てるのは**空いているところだけ**。公式表記が入っていればそれを残す。
    用語集にも無ければ日本語のまま出す（推測の表記は作らない）。
    """
    names = payload.get("names") or {}
    ja = names.get("ja")
    ja_text = ja.get("text") if isinstance(ja, dict) else getattr(ja, "text", None)
    if not ja_text:
        return
    for locale, table in glossary.items():
        if names.get(locale):
            continue
        found = table.get(ja_text)
        if found is not None:
            names[locale] = found.model_dump()
    payload["names"] = names


def _page_fetched_at(ws: Workspace, entries: list[dict[str, Any]]) -> dict[str, str]:
    """情報源ごとの「巡回先のページを最後に読めた日時」（巡回の状態から。いまの巡回先だけ）。

    巡回の状態の `fetched_at` は、200 でも 304（変わっていない）でも進み、失敗では進まない。
    巡回間隔の上限は 7 日なので、正常に巡回していれば 14 日（`stale_after_days`）は超えない。
    """
    try:
        urls = json.loads((ws.state_dir / "crawl.json").read_text(encoding="utf-8")).get("urls")
    except (OSError, ValueError):
        return {}
    out: dict[str, str] = {}
    for entry in entries:
        stamps = [
            str((urls or {}).get(page["url"], {}).get("fetched_at") or "")
            for page in entry.get("pages") or []
        ]
        latest = max((s for s in stamps if s), default="")
        if latest:
            out[entry["id"]] = latest
    return out


def _crawl_state(ws: Workspace) -> CrawlState:
    """巡回の状態。読めなければ空（鮮度はどれも抽出した時刻のまま据え置かれる）。"""
    try:
        return CrawlState.load(ws.state_dir / "crawl.json")
    except (OSError, ValueError):
        return CrawlState()


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def _refreshed(stamp: Any, urls: set[str], state: CrawlState) -> Any:
    """事実の鮮度を、その出どころのページを最後に読めた日時まで進める（2026-09-28）。

    事実の `*_fetched_at` は**抽出した時刻**で、ページを読み直しても中身が変わっていなければ
    抽出し直さないので進まない。そのため最初の抽出（9/12〜14）から 14 日たった 9/26 ごろから、
    毎日読めている施設まで「公式ページの取得が 14 日以上できていない」に落ちていた（9/28 の月曜に
    不明 152 件）。変わっていないページを読めたなら、そこから取った事実はその時点でも正しい。

    出どころのページを**すべて**読めているときだけ進め、いちばん古く読めた日時を採る。1 枚でも
    読めた記録が無ければ（取得の失敗・URL の食い違い）、抽出した時刻のまま据え置く。
    「読めた」の判定は sitemill の `read_at`（提案 L、sitemill ADR 0028）。失敗が無く、
    抽出したときから中身が変わっていないページだけを数える。中身が変わったのに抽出が失敗した晩に、古い事実を新しく
    扱わない（2026-09-29 に切り替え。それまでは読めた時刻だけを見ていた）
    """
    read = read_at(state, urls)
    if read is None:
        return stamp
    current = _parse(stamp)
    return read.isoformat() if current is None or read > current else stamp


def _evidence_urls(items: Any) -> set[str]:
    return {
        str((item.get("evidence") or {}).get("source_url"))
        for item in items or []
        if isinstance(item, dict) and (item.get("evidence") or {}).get("source_url")
    }


def records_path(ws: Workspace, source_id: str) -> Path:
    return ws.records_dir / f"{source_id}.jsonl"


def load_records(ws: Workspace, source_id: str) -> list[dict[str, Any]]:
    return RecordStore(records_path(ws, source_id)).all()


@dataclass
class Dataset:
    """ページ生成が読む一式。"""

    sources: list[Source]
    spots: list[Spot]
    operators: list[TransportOperator]
    routes: list[Route]
    by_source: dict[str, Source] = field(default_factory=dict)
    spots_by_area: dict[str, list[Spot]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.by_source = {s.id: s for s in self.sources}
        self.spots_by_area = {}
        for spot in self.spots:
            self.spots_by_area.setdefault(spot.area, []).append(spot)

    @classmethod
    def load(cls, ws: Workspace) -> Dataset:
        """YAML の枠に、レコードとして保存された事実を重ねて読む。"""
        entries = load_entries(ws)
        sources = [Source.model_validate(e) for e in entries]
        glossary = load_glossary(ws)
        spots: list[Spot] = []
        operators: list[TransportOperator] = []
        routes: list[Route] = []
        read_at = _page_fetched_at(ws, entries)
        fetched = _crawl_state(ws)
        # 共有ページ（covers を持つ情報源）の告知。施設を持つ情報源とは別のファイルに入る
        shared_notices: dict[str, list[dict[str, Any]]] = {}
        # 共有ページのうち告知を読む URL（施設ごと）。告知の鮮度はこのページを読めた日時でも測る
        shared_pages: dict[str, set[str]] = {}
        for entry in entries:
            if not entry.get("covers"):
                continue
            pages = {p["url"] for p in entry.get("pages") or [] if p.get("kind") == "shared_notice"}
            for covered in entry["covers"]:
                shared_pages.setdefault(covered, set()).update(pages)
            for record in load_records(ws, entry["id"]):
                spot_id = record.get("spot_id")
                if spot_id:
                    shared_notices.setdefault(spot_id, []).extend(record.get("notices") or [])
        for entry in entries:
            stored = {
                r.get("spot_id") or r.get("route_id"): r for r in load_records(ws, entry["id"])
            }
            spot = spot_from_entry(entry)
            if spot is not None:
                record = stored.get(spot.spot_id)
                payload = {**spot.model_dump(), **record} if record else spot.model_dump()
                payload["page_fetched_at"] = read_at.get(entry["id"])
                # 鮮度は抽出した時刻ではなく、出どころのページを最後に読めた時刻（_refreshed）
                payload["hours_fetched_at"] = _refreshed(
                    payload.get("hours_fetched_at"),
                    _evidence_urls(payload.get("hours")) | _evidence_urls(payload.get("closures")),
                    fetched,
                )
                notice_pages = {
                    p["url"] for p in entry.get("pages") or [] if p.get("kind") == "notice"
                }
                payload["notices_fetched_at"] = _refreshed(
                    payload.get("notices_fetched_at"),
                    notice_pages
                    or _evidence_urls(payload.get("notices"))
                    or shared_pages.get(spot.spot_id, set()),
                    fetched,
                )
                _fill_names(payload, glossary)
                extra = shared_notices.get(spot.spot_id) or []
                if extra:
                    # 引用で重複を落とす（同じ告知が自館のページと共有ページの両方に出る）
                    seen = {
                        (n.get("evidence") or {}).get("quote") for n in payload.get("notices") or []
                    }
                    payload["notices"] = [
                        *(payload.get("notices") or []),
                        *(n for n in extra if (n.get("evidence") or {}).get("quote") not in seen),
                    ]
                spots.append(Spot.model_validate(payload))
            operator = operator_from_entry(entry)
            if operator is not None:
                operators.append(operator)
            for route in routes_from_entry(entry):
                record = stored.get(route.route_id)
                routes.append(
                    Route.model_validate({**route.model_dump(), **record}) if record else route
                )
        return cls(sources=sources, spots=spots, operators=operators, routes=routes)

    def spots_in(self, area_slug: str) -> list[Spot]:
        return sorted(self.spots_by_area.get(area_slug, []), key=lambda s: s.spot_id)

    def routes_of(self, operator_id: str) -> list[Route]:
        return [r for r in self.routes if r.operator_id == operator_id]
