"""公式表記も Wikipedia も無い施設名に、用語集の固定訳を作る（ADR 0005 の経路 4）。

`tools.resolve_names` の経路 1〜3（公式サイトの英語ページ／自治体・観光協会の英語ページ／
Wikipedia の言語間リンク）で確定できなかった施設だけを対象にする。ここで作るのは
**凍結した固定訳**で、由来が分かるように `source: glossary` と注記を残し、差分でレビューする。

守ること:
- 施設名しか渡さない。営業時間・料金などの**事実は LLM に触らせない**（ADR 0002）
- 出力は名前だけ。説明・注記・読み方を混ぜない
- 繁体字は、日本の新字体を繁体字の字形に直す（県→縣、沢→澤）。簡体字は使わない
- 固有名詞の部分はヘボン式、一般名詞の部分は英語の慣用訳（美術館→Museum of Art など）
- 1 語も訳せないものは入れない（日本語のまま出す）

使い方:
    uv run python -m tools.translate_names --limit 5      # 見るだけ
    uv run python -m tools.translate_names --apply
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import yaml
from sitemill.clock import jst_now
from sitemill.extract.llm import make_provider
from sitemill.settings import Workspace

from japan_open_today.data import Dataset

LOCALES = ("en", "zh-Hant")
SIMPLIFIED = re.compile(r"[国馆术园际标区丰历产权义习书体乐云价众优会伟传伤]")
KANA = re.compile(r"[ぁ-んァ-ヶ]")
HAN = re.compile(r"[一-龥]")
LATIN = re.compile(r"[A-Za-z]")

SYSTEM = """あなたは日本の観光施設の名前を、英語と繁体字中文の表記にする係です。

渡されるのは施設名だけです。名前以外のことは書かないでください。

## 守ること
1. **名前だけを返す。** 説明・読み方・注記・括弧書きを足さないでください。
2. **英語**: 一般名詞の部分は英語の慣用訳（美術館→Museum of Art / Art Museum、博物館→Museum、
   資料館→Museum、記念館→Memorial Museum、公園→Park、庭園→Garden、神社→Shrine、寺→Temple、
   城→Castle、温泉→Onsen、道の駅→Roadside Station）。固有名詞の部分はヘボン式のローマ字
   （長音の記号は付けない。「大坂」→ Osaka ではなく Osaka のように一般的な綴りを使う）。
3. **繁体字中文**: 漢字はそのまま使い、**日本の新字体を繁体字の字形に直す**
   （県→縣、沢→澤、駅→站ではなく駅は「車站」ではなく固有名詞の一部ならそのまま）。
   カタカナの部分は意味の通る中文にする。**簡体字は使わない**。
4. 訳せない、または自信が持てない場合は、その言語を `null` にしてください。
   **推測で当てるより、日本語のまま出すほうが正しいです。**
"""
SCHEMA = {
    "type": "object",
    "properties": {
        "en": {"type": ["string", "null"], "description": "英語表記。無理なら null"},
        "zh_hant": {"type": ["string", "null"], "description": "繁体字中文の表記。無理なら null"},
    },
    "required": ["en", "zh_hant"],
    "additionalProperties": False,
}


def _ok(text: str | None, locale: str) -> bool:
    """その言語の表記として使えるか（字種で見る。`resolve_names` と同じ考え方）。"""
    if not text or len(text.strip()) < 2 or len(text) > 60:
        return False
    text = text.strip()
    if locale == "en":
        return bool(LATIN.search(text)) and not KANA.search(text) and not HAN.search(text)
    return bool(HAN.search(text)) and not KANA.search(text) and not SIMPLIFIED.search(text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--apply", action="store_true", help="用語集に書く")
    parser.add_argument("--out", type=Path, default=Path("data/runs/name-translation.json"))
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    ds = Dataset.load(ws)
    todo = [s for s in ds.spots if any(not s.names.get(loc) for loc in LOCALES)]
    if args.limit:
        todo = todo[: args.limit]
    print(f"== 固定訳を作る施設 {len(todo)} 件 ==")
    if not todo:
        return 0

    llm = ws.site.llm
    provider = make_provider(
        ws.secrets.sitemill_llm_provider or llm.provider,
        secrets=ws.secrets,
        cache_dir=ws.llm_cache_dir,
    )
    rows: list[dict[str, str]] = []
    skipped: list[tuple[str, str]] = []
    tokens_in = tokens_out = 0
    for n, spot in enumerate(todo, 1):
        ja = spot.name("ja")
        # 名前がもともとラテン文字だけなら、それがその施設の表記である（訳さない）。
        # 「NAGARE」を英語名にできずに落としていた
        if not KANA.search(ja) and not HAN.search(ja) and LATIN.search(ja):
            marks = []
            for locale in LOCALES:
                if spot.names.get(locale):
                    continue
                rows.append({"ja": ja, "locale": locale, "text": ja, "spot_id": spot.spot_id})
                marks.append(f"{locale}={ja[:26]}")
            print(f"  {n:3}/{len(todo)} {ja[:18]:20} {' '.join(marks)}（元からラテン文字）")
            continue
        result = provider.complete_json(
            system=SYSTEM,
            user=f"施設名: {ja}\n所在地: 香川県（{spot.area}）\n種別: {spot.category}",
            schema=SCHEMA,
            model=llm.model,
            max_tokens=200,
            temperature=0.0,
        )
        tokens_in += result.input_tokens or 0
        tokens_out += result.output_tokens or 0
        got = {"en": result.data.get("en"), "zh-Hant": result.data.get("zh_hant")}
        marks = []
        for locale in LOCALES:
            if spot.names.get(locale):
                continue
            text = (got.get(locale) or "").strip()
            if not _ok(text, locale):
                skipped.append((spot.spot_id, locale))
                continue
            rows.append({"ja": ja, "locale": locale, "text": text, "spot_id": spot.spot_id})
            marks.append(f"{locale}={text[:26]}")
        print(f"  {n:3}/{len(todo)} {ja[:18]:20} {' '.join(marks) or '（訳せない）'}", flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "translated_at": jst_now().isoformat(),
                "model": llm.model,
                "input_tokens": tokens_in,
                "output_tokens": tokens_out,
                "rows": rows,
                "skipped": skipped,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    print(
        f"\n訳せた {len(rows)} 件 / 訳せない {len(skipped)} 件 / 入力 {tokens_in} 出力 {tokens_out}"
    )
    if args.apply and rows:
        for locale in LOCALES:
            _write(ws, locale, [r for r in rows if r["locale"] == locale], llm.model)
    elif args.apply:
        print("書くものが無い")
    return 0


def _write(ws: Workspace, locale: str, rows: list[dict[str, str]], model: str) -> None:
    if not rows:
        return
    path = ws.data_dir / "glossary" / f"{locale}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    data = yaml.safe_load(text) if text else None
    data = data or {"names": {}}
    names = data.setdefault("names", {})
    added = 0
    for row in rows:
        if row["ja"] in names:
            continue  # 凍結。変えるときは差分でレビューする
        names[row["ja"]] = {
            "text": row["text"],
            "source": "glossary",
            "url": "",
            "checked_on": jst_now().date().isoformat(),
            "note": f"固定訳（公式表記・Wikipedia が無いため {model} で作成。差分でレビューする）",
        }
        added += 1
    header = "\n".join(line for line in text.split("\n") if line.startswith("#"))
    path.write_text(
        (header + "\n" if header else "")
        + yaml.safe_dump(data, allow_unicode=True, sort_keys=True, width=100),
        encoding="utf-8",
        newline="\n",
    )
    print(f"  {path}: {added} 件を追加（合計 {len(names)} 件）")


if __name__ == "__main__":
    raise SystemExit(main())
