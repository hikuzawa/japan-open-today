"""データの読み込み。`data/sources/*.yaml`（情報源と巡回設定）と `data/records/*.jsonl`（事実）。

1 つの YAML エントリが sitemill の `Source`（巡回設定）と、このサービスの `spot` または
`transport`（表示用の設定）を兼ねる。akiya-atlas と同じ流儀。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
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
        # 共有ページ（covers を持つ情報源）の告知。施設を持つ情報源とは別のファイルに入る
        shared_notices: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            if not entry.get("covers"):
                continue
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
