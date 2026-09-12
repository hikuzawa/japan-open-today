# Japan Open Today

香川県の観光施設と交通について、「今日・今週、行けるか」を日本語・英語・繁体字で提供する静的サイト。

- 営業時間・定休日・臨時休業・運休・料金・予約要否・アクセスを**公式サイトから毎日取得**し、鮮度を武器にする
- 事実は原文からの機械抽出のみ（`FieldValue` に原文の引用を残す）。公式の説明文は転載しない
- 「今日開いているか」は定休日の規則・祝日・臨時休業の告知から計算し、**判定の根拠を表示する**。不明なら「不明」と出す
- 共通エンジン [sitemill](../sitemill) の 2 つ目の利用者。設計判断は [`docs/adr/`](docs/adr/)

## 使い方

```sh
uv sync
uv run pytest
uv run sitemill run          # crawl → extract → heal → build
uv run python -m http.server -d dist 8000
```

`ANTHROPIC_API_KEY` は `.env` に手で書く（`.env.example` を参照）。

人間側で必要な作業は [`docs/human-tasks.md`](docs/human-tasks.md)。
