# お問い合わせフォーム（Google フォーム + Apps Script + AI 自動返信・3 言語）

運営者の連絡先として使う Google フォームを、Apps Script の `setup()` 一発で用意する。
送信されると内容を AI（Anthropic API、Haiku 4.5）が分類し、**回答者の言語で**返信文を作って
自動返信し、対応が必要な種別は GitHub Issue を作る。

```
送信 → onFormSubmit → 分類・返信文生成（Anthropic API） → 自動返信（回答者の言語）
                                                    → GitHub Issue（種別による）
                                                    → 自動返信ログ（スプレッドシート）
                                                    → 運営者へ通知メール（日本語の要約）
```

## akiya-atlas の同名の仕組みとの違い

土台は `akiya-atlas/tools/contact_form/` と同じで、変えたのは 2 点だけ。

1. **種別が別**。掲載内容の誤りへの正しい対応は「ページを隠す」ではなく「直す」なので、
   akiya の `takedown`（非表示にする印）は持たない。`correction` の Issue は**作業項目**で、
   情報源の見直しか読み取りの修正につながる
2. **3 言語**。設問は 3 言語併記の 1 フォームにし、各言語のページからは「返信の言語」を
   埋めた prefill 付きリンクで開く。自動返信はその言語で書く（定型の断り書き・署名・受領文は
   `AutoReply.gs` に 3 言語分ある）。運営者への通知と Issue は日本語

共用しない理由: akiya のフォームは分類も返信文も空き家専用で、Issue も `hikuzawa/akiya-atlas`
に立つ。共用すると香川の問い合わせに空き家の案内が返る。

## ファイル
- `Code.gs` … `setup()`（フォーム・回答シート・ログシート・送信時トリガー・GitHub ラベルの作成と、
  **言語ごとの prefill URL の出力**）、`reset()`、送信時の入口 `onFormSubmit`
- `AutoReply.gs` … 分類と返信文の生成、自動返信、Issue 作成、ログ、運営者通知、`checkSetup()`、`previewReply()`
- `appsscript.json` … タイムゾーン（Asia/Tokyo）と V8 ランタイム

## 種別と対応

| AI の分類 | 対象 | 回答者への自動返信 | GitHub Issue |
|---|---|---|---|
| `correction` | 掲載している事実が違う・古い・消えている | 受領と、一次情報と読み取りを確認して直す旨（期限は約束しない）。対象 URL を復唱。**削除するとは書かない** | ラベル `correction` |
| `facility` | 施設・交通事業者・自治体からのご連絡 | 受領と、運営者が確認して返答する旨。掲載の方針を一言 | ラベル `facility` |
| `question` | 行き方・開いているか・料金などの質問 | 当サイトは施設の窓口ではないことと、その施設のページの根拠・公式リンクで確認する案内。**「今日は開いています」のような断定はしない** | なし |
| `partnership` | 取材・提携、判定に迷うもの | 受領のみ（定型文） | ラベル `needs-human` |
| `spam` | 営業・スパム | 返信しない | 作らない（ログにだけ残す） |

安全側の扱い（akiya と同じ）:
- 確信度 0.6 未満は受領のみに寄せる。`spam` は 0.85 以上のときだけ採る
- API キー未設定・API エラー・想定外の出力のときは返信せず、`needs-human` の Issue を作って通知する
- 同一メールアドレスへの自動返信は 24 時間に 1 通まで
- 返信文の冒頭に「自動返信であり、分類と文面の作成に AI を使っている」旨を必ず入れる
- 返信先は入力欄の値だけ（Google ログインは求めない）。他人のアドレスを入力された場合に備え、
  返信は 24 時間に 1 通、冒頭に自動返信の明記、問い合わせ本文は返信に載せない
- Issue には氏名・メールアドレスを書かない（回答スプレッドシートの同時刻の行を参照する）
- 問い合わせ本文はデータとして扱い、本文中の指示には従わない（プロンプトで明示し、出力は
  定型のツール呼び出しに限る）

## setup() を実行する前に（人間側の作業）

### 1. スクリプトプロパティに入れる値
Apps Script エディタ → 左の歯車「プロジェクトの設定」→「スクリプト プロパティ」。
値はコードにも Git にも書かない。

| キー | 必須 | 値 |
|---|---|---|
| `ANTHROPIC_API_KEY` | 必須 | Anthropic の API キー。このフォーム専用に 1 本作り、月間の利用上限を小さくしておく（1 件 1 円未満の目安） |
| `GITHUB_TOKEN` | 必須 | Fine-grained personal access token。Repository access: `hikuzawa/japan-open-today` のみ、Permissions → Issues: Read and write |
| `GITHUB_REPO` | 必須 | `hikuzawa/japan-open-today` |
| `NOTIFY_TO` | 任意 | 運営者の通知先メール。未設定なら `setup()` を実行した Google アカウント宛て |
| `SITE_BASE_URL` | 任意 | `https://japan-open-today.com`（既定） |
| `OPERATOR_NAME` | 任意 | 署名に使う運営者名。未設定なら `Japan Open Today`（`site.toml` の `[operator] name` と合わせる） |
| `ANTHROPIC_MODEL` | 任意 | 既定は `claude-haiku-4-5` |

### 2. 承認する権限（初回実行時）
Google フォーム／スプレッドシート／ドライブの操作、メールの送信、外部サービスへの接続
（Anthropic API と GitHub API）。

### 3. `setup()` を実行する
実行ログに次が出る。

- フォームの回答用 URL（**これを `site.toml` の `[operator] contact` に入れる**）
- **prefill（ja / en / zh-Hant）の URL**。`i18n/<locale>.yaml` の `contact.url` に入れると、
  各言語のページの「連絡先」がその言語の prefill 付きで開く
- 回答スプレッドシートの URL、登録したトリガー、作成した GitHub ラベル

### 4. 確かめる
- `checkSetup()` … 設定と接続の確認（メールも Issue も送らない）
- `previewReply()` … 繁体字と英語の見本で、分類と返信文を確認（何も送らない）

## 変えたいとき
- 種別・文言 → `Code.gs` の `CONFIG` と `AutoReply.gs` の定数を直して `setup()` を再実行
  （既存フォームの設問は変わらない。作り直すときは `reset()` のあと `setup()`）
- 返信の書き方 → `AutoReply.gs` の `systemPrompt_`
