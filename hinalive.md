# 概要

任意のPCゲームをプレイ中に、キャラが画面の状況を**実況**したり**応援**したりしてくれる汎用ゲーム実況支援AIコンパニオン。
バックグラウンドで常に稼働しており、キャラ表示や音声再生はブラウザから行う。
プレイヤーは音声 or テキストで AI と会話でき、ゲームプレイの相棒として横で盛り上がってくれる。

メッセージ生成に Gemini API、TTS に ElevenLabs を使用する。
自宅用・自分用ソフトであり、インターネットに公開されることはない。
稼働環境もローカルPCでのみ稼働させる。

## コンセプト：相棒型（実況＋応援の両刀）

- 画面状況を読み取って**実況**する（何が起きているかを言語化）
- 状況に応じて**応援・煽り・労い・茶々入れ**を入れる
- プレイヤーからの問いかけに**雑談**で返す
- 1人プレイのゲームに「横にいてくれる人」感を出すのが目的

## ゲームタイトルの切り替え方針

- ゲームタイトルごとに専用の**実況応援プロンプトファイル**（例：`prompts/zwift.md`, `prompts/minecraft.md`）を用意する
- 切り替えは**手動**：`config.yaml` の `prompts.task_path` で active なファイルを指定する
- キャラ設定（`prompts/character.md`）はゲームに関わらず常時ロード
- フロントエンドや実行時APIからの切り替え機能は持たない（必要になったら検討）

# 稼働環境

- Windows 11
- スピーカーとマイクがあること（エコー対策のためヘッドセット推奨）
- ブラウザは Google Chrome Desktop
- ゲームは特定タイトルに依存しない（Windows 上で表示されるウィンドウであれば何でも）
- 画面キャプチャは外部ソフト不要。Python 側で `mss` + `pygetwindow` を用いて、対象ウィンドウを直接取得する

# 技術スタック

## バックエンド
- Python 3.13
- FastAPI
- uvicorn（ローカルサーバ、ポート 8000、http）
- SQLite3
- 使用AIモデル：`gemini-3.1-pro-preview`,`gemini-3.5-flash` ※切り替え可能

## フロントエンド
- HTML / CSS / JavaScript（素のJS）
- Web Speech API（音声入力）
- ElevenLabs API（TTS）

# フォルダ構成

```
hinalive/
├── backend/
│   ├── main.py
│   ├── config.yaml          # クリップ座標、Gemini APIキー、モデル名、active プロンプト指定 等
│   ├── prompts/
│   │   └── summary.md       # 中期記憶要約テンプレ（キャラ非依存）
│   ├── characters/
│   │   └── <char_id>/       # キャラごとに 1 ディレクトリ（例: hina）
│   │       ├── setting.yaml # キャラ名 (name) / ElevenLabsボイスID (voice_id)
│   │       └── prompts/
│   │           ├── character.md  # キャラ設定プロンプト（常時ロード）
│   │           ├── cheer.md      # 実況応援プロンプト（既定）
│   │           ├── zwift.md      # ゲーム別の実況応援プロンプト（--cheer で切替）
│   │           └── ...
│   ├── requirements.txt
│   └── data.db              # SQLite
├── frontend/
│   ├── index.html
│   ├── app.js
│   ├── config.js            # ElevenLabs APIキー等（フロント設定）
│   ├── style.css
│   └── images/
│       └── <char_id>/       # キャラごとに 1 ディレクトリ
│           ├── stand.png    # キャラ立ち絵（APNG）
│           └── talk.png     # キャラ会話絵（APNG）
├── captures/
│   └── processing/          # キャプチャ画像（加工後JPG）の保存フォルダ
└── setup.ps1                # 初期設定スクリプト（PowerShell用、venv作成 + pip install）
```

## キャラクター追加方法

新キャラクターを追加するには、キャラクターID（A-Za-z 1-16文字）を `<char_id>` として下記ファイルを配置するだけでよい：

- `backend/characters/<char_id>/setting.yaml` … `name` / `voice_id`
- `backend/characters/<char_id>/prompts/character.md` … キャラ設定プロンプト
- `backend/characters/<char_id>/prompts/cheer.md` … 既定の実況応援プロンプト
- `frontend/images/<char_id>/stand.png` … 立ち絵
- `frontend/images/<char_id>/talk.png` … 会話絵

`config.yaml` の `character.current_id`、または起動オプション `--char_id` で切り替える。
`char_id` と `voice_id` はバックエンド `/api/character` 経由でフロントへ渡される。

# 設定ファイル仕様

## backend/config.yaml
- Gemini APIキー
- 使用モデル名（`gemini-3.1-pro-preview` / `gemini-3.5-flash`）と起動時デフォルト
- 対象ウィンドウタイトル（部分一致、`capture.window_title`）
- 加工後画像の保存フォルダ（`capture.processing_dir`）
- キャプチャ画像のクリップ座標（ウィンドウ左上原点。すべて0なら無効）
- リサイズ後の解像度（横幅 px。0なら リサイズなし）
- 会話履歴の取得件数（既定 30件）
- メインループ間隔（既定 10秒）
- DBファイルパス
- プロンプトファイルパス
  - `prompts.summary_path` … 中期記憶要約用テンプレート（`{target_chars}` `{history_text}` `{game_name}` を埋め込み）
  - キャラ設定（`character.md`）と実況応援（`cheer.md`）のパスは
    `backend/characters/<character.current_id>/prompts/` から自動で解決される。
    cheer の別ファイル指定は起動オプション `--cheer` を使う
- キャラクター設定
  - `character.current_id` … 現在のキャラクターID。会話履歴・各種記憶テーブルに紐づくキー。**A-Za-z 1〜16文字** の制約（起動時に検証、違反したら起動失敗）
  - 起動オプション `--char_id` で上書き可能
  - `backend/characters/<character.current_id>/setting.yaml` から `name` / `voice_id` を読み込む（起動時必須）
- ゲーム設定
  - `game.name` … 現在プレイ中のゲームの表示名。要約プロンプト等に埋め込まれる
- 記憶レイヤー設定
  - `memory.mid_term.window_size` … 中期記憶生成時に取得する直近会話件数（既定 30）
  - `memory.mid_term.target_chars` … 要約の目標文字数（既定 100）
  - `memory.mid_term.batch_threshold` … 前回処理時から何件の会話が増えたらバッチを走らせるか（既定 20）
  - `memory.mid_term.interval_seconds` … バッチループの待機間隔秒数（既定 10）

### 起動時オプション
- `--char_id <ID>` … `character.current_id` を実行時に上書き（A-Za-z 1-16文字）
- `--window <タイトル部分一致>` … `capture.window_title` を実行時に上書き
- `--cheer <ファイル名>` … 実況応援プロンプトファイルを
  `backend/characters/<char_id>/prompts/` 配下から指定して上書き（拡張子省略可）
- `--day <整数>` … プレイ日 (Day) を整数で指定（`1` のような連番でも `20260513` のような日付運用でも可）。
  - 未指定時は、現キャラの `messages` で最新 `created_at` の行の `day` を取得して **継続**
  - 履歴が1件もなければ `1` で新規開始
- `--longterm-batch` … 長期記憶バッチを実行して終了する。`--char_id` と `--day` の併用が必須。
  - 指定キャラ・指定 Day の中期記憶を1件ずつ Gemini（flash 固定）に投げて
    キーワード（1つ以上）と印象度（1-5）を抽出し、`long_term_keywords` テーブルに保存する
  - すでに同じ `mid_term_memory_id` でキーワードが入っている場合はスキップ（再実行安全）
  - 例：`python backend\main.py --longterm-batch --char_id hina --day 1`

## frontend/config.js
- ElevenLabs APIキー
- ElevenLabs ボイスID 等

※ ElevenLabs APIキーをフロントに置くのは本来危険だが、自分専用ソフトのため許容。
　将来的にバックエンド経由でプロキシする実装に切り替えることも可能。

## プロンプトファイル

- `backend/characters/<char_id>/prompts/character.md` … キャラ設定（口調・性格・世界観など）。**常にロード**される。
- `backend/characters/<char_id>/prompts/cheer.md` … 既定の実況応援プロンプト。
- `backend/characters/<char_id>/prompts/<game>.md` … タイトル別の実況応援プロンプト。「画面から何を読み取るか」「どんな声かけをするか」「禁止事項」などを記述する。
  - 例：`zwift.md`, `minecraft.md` などを必要に応じて追加し、`--cheer <name>` で切り替える
- `backend/prompts/summary.md` … 中期記憶要約バッチで使うテンプレート（キャラ非依存）。Python の `str.format()` で以下の名前付きプレースホルダを埋め込んで使う：
  - `{game_name}` … `config.yaml` の `game.name`
  - `{target_chars}` … `config.yaml` の `memory.mid_term.target_chars`
  - `{history_text}` … 直近会話履歴（`AI: ～` / `プレイヤー: ～` 形式）
  - テンプレート内に意図しない `{` `}` を書きたい場合は `{{` `}}` でエスケープする

# バックエンド仕様

バックエンドは FastAPI 起動時に **3本の asyncio バックグラウンドタスク** を生やす
（API リクエスト処理をブロックしないため、必ず非同期で実装する）：

1. **メインループ**：キャプチャ → 応援メッセージ生成
2. **中期記憶ループ**：会話履歴の要約バッチ（独立した間隔で動く）。要約挿入後に好感度（affection）の判定も行う
3. **感情ループ**：直近会話履歴から happy / tension / safe の変化を判定し emotions を更新（60秒間隔）

DB操作（会話履歴・各種記憶テーブル）はすべて `character.current_id` を WHERE 条件に含み、
現在のキャラクターに紐づくレコードだけを読み書きする。

## Day（プレイ日）の概念

- 1回のプレイセッションを区切るための整数値。
  - `1` のような連番運用（1日目、2日目…）と、`20260513` のような日付運用（YYYYMMDD）のどちらも想定。バックエンドは単に整数として扱う。
- 起動時に **現在の Day を1つだけ確定** し、そのプロセスが終了するまで固定で使う（`app.state.current_day`）。
- 確定ルール：
  1. `--day <整数>` が指定されていればその値
  2. なければ現キャラの `messages` で最新 `created_at` の行の `day` を採用（前回プレイの継続）
  3. 履歴が1件もなければ `1`
- 会話履歴・中期記憶レコードを **書き込む際は必ず現在の Day を埋める**。
- メッセージ生成プロンプト用の **直近30件履歴取得は `day = 現在のDay` で絞り込む**（過去 Day のログは混ぜない）。
- 中期記憶バッチの起動判定・要約対象・`last_message_id` 管理もすべて現 Day スコープ（後述）。

## メインループ処理

1. `pygetwindow` で `capture.window_title` の部分一致するウィンドウを探す
   - 最小化されていない、サイズが正のものを採用
   - 見つからなければ **警告ログを出してこのループをスキップ**（次の interval まで待つ）
2. `mss` でそのウィンドウ領域をスクリーンショット → PIL.Image に変換
3. `image.clip` で追加クリップ（ウィンドウ左上原点）→ `image.resize_width` でリサイズ
4. `captures/processing/<unique>.jpg` として JPEG 保存（生PNGは保持しない）
5. DBから現キャラ・**現在の Day** の直近30件の会話履歴、および現キャラの直近10件の中期記憶を取得（どちらも古い順に整列）
6. プロンプト構築：
   - **system_instruction**：キャラ設定プロンプト（`character.md`）＋実況応援プロンプト（`config.yaml` で指定された active な game プロンプト）＋現在の感情値ブロックを `---` 区切りで連結
     - 感情値ブロックは `emotions` テーブルから現キャラの `happy` / `tension` / `safe` / `affection` を取得し、`0-100` の数値に定性ラベル（高め / やや高め / 普通 / やや低め / 低め）を付けて整形する
   - **user content**：
     - 「本日のミッション：」：現キャラ・現 Day の `missions.content`（未設定時はブロックごと省略）
     - 「直近の会話履歴（古い順）」：`AI: ～` / `プレイヤー: ～` の繰り返し形式
     - 「ここまでのプレイの概要（古い順）」：中期記憶の要約を段落区切りで列挙
     - 「関連した過去の出来事：」：`recollections` テーブルに該当キャラのレコードがある場合のみ追加。`mid_term_memories` を `id` 古い順に引き、1行ずつ `ゲーム名：{game_name} 出来事：{summary}` 形式で列挙する（複数ゲームにまたがり得るため `game_name` を明記。`game_name` が NULL の古い行は `（不明）` に置換）。レコードが無い場合はブロックごと省略
     - 最新キャプチャ画像1枚を添付（履歴に画像は含めない）
7. Gemini API に投げて、実況・応援メッセージを生成
8. 生成されたメッセージをDBに保存（`character_id`=現キャラ、`speaker="ai"`、未再生フラグ=未再生）
9. 5秒おきに次のユーザーメッセージを待つ。次のメッセージが来るか、所定秒数繰り返して、メッセージが来なければループ先頭へ戻る

## 中期記憶バッチループ

要約モデルは **常に flash 固定**（コスト・遅延優先）。
判定・要約・記録のすべては **「現キャラ かつ 現在の Day」** スコープで動作する。

1. `last_message_id = MAX(mid_term_memories.last_message_id WHERE character_id=current AND day=current_day)` を取得。レコードがなければ 0。
2. `messages WHERE character_id=current AND day=current_day AND id > last_message_id` の件数を数える
3. 件数が `batch_threshold`（既定 20）未満ならスキップして手順5 へ
4. 件数が `batch_threshold` 以上の場合：
   - `latest = MAX(messages.id WHERE character_id=current AND day=current_day)` を取得（＝今回の対象レコードのうち最大 id）
   - 現キャラ・現 Day の直近 `window_size` 件（既定 30件）の会話履歴を取得
   - `prompts/summary.md` テンプレートを `{target_chars}` `{history_text}` `{game_name}` で埋めてプロンプト化
   - flash モデルで要約 → `mid_term_memories` テーブルに `(character_id, summary, day=current_day, last_message_id=latest, game_name=config.yaml の game.name)` を追加
   - **続けて好感度判定**：保存した `summary` を `{memory_text}` として `call_affection_judge` を呼び、`emotions.affection` に `±affection_delta` を反映（0-100 クランプ）。Gemini 呼び出しの頻度を要約と独立に変える可能性があるため、要約と1回にまとめず別途呼び出す
5. `interval_seconds`（既定 10秒）待ってループ先頭へ戻る

※ 中期記憶はメインループの会話生成プロンプトに **「ここまでのプレイの概要（古い順）」** として注入される（直近10件、古い順）。

## 長期記憶バッチ（CLI 実行）

長期記憶は、中期記憶にキーワードを紐付けることで後で検索・想起できるようにする仕組み。
**Day が終わったタイミングで手動 or 別バッチから CLI コマンドで実行する**。バックグラウンドループでは走らせない。

起動コマンド：
```
python backend\main.py --longterm-batch --char_id <ID> --day <整数>
```

判定モデルは **flash 固定**。処理内容：

1. 指定 `char_id` と `day` で `mid_term_memories` の全行を古い順に取得する
2. 各行を1件ずつ処理：
   - すでに `long_term_keywords.mid_term_memory_id` にレコードがあればスキップ（再実行安全）
   - Gemini に `mid_term_memories.summary` を投げて、以下を抽出してもらう：
     - **キーワード**：1つ以上（複数可）。固有名詞・行動・感情・トピックなど、後で検索に使える短い単語句
     - **印象度**：その出来事がキャラにとってどれだけ印象深いかを 1〜5 の整数で評価
   - 出力は `response_mime_type=application/json` + `response_schema` で次の JSON に強制：
     - `{"keywords": ["<キーワード>", ...], "impression": <1-5>}`
   - プロンプトに埋め込む `game_name` は `mid_term_memories.game_name`、NULL なら現行 `config.yaml` の `game.name` をフォールバックとして使う
3. 抽出したキーワード1つにつき1行、`long_term_keywords` に `(character_id, keyword, mid_term_memory_id, impression)` を挿入する（同じ中期記憶に紐づく全行で `impression` は同値）

長期記憶を **思い出す** ロジック（プレイ中にキーワード検索して関連 mid_term を呼び出し、生成プロンプトに注入する処理）は本フェーズの対象外。

## 感情ループ

感情値のうち `happy` / `tension` / `safe` の更新を 60 秒間隔で行う独立ループ。`affection` は本ループでは扱わず、中期記憶ループ側で更新する。判定モデルは flash 固定。
**長期記憶の想起（回想テーブル更新）も本ループに相乗りする** ことで、感情判定と同じ会話履歴範囲・同じ Gemini 呼び出し1回で済ませる。

1. `characters` マスターから現キャラの delta 値を取得（未登録なら警告ログを出してスキップ）
2. 現キャラ・現 Day の直近 30 件の会話履歴を取得（履歴ゼロならスキップ）
3. 履歴と `game_name` / `char_name` を埋め込んで `call_emotion_judge` を呼ぶ。出力は `response_mime_type=application/json` + `response_schema` で次のJSONに強制：
   - `{"happy": "すごく嬉しい|嬉しい|普通", "tension": "すごく上がる|上がる|上がらない", "safe": "すごく安心|安心|普通", "keywords": ["<キーワード1>", ...]}`
   - `keywords` は会話履歴から抽出した1つ以上の検索用短い単語句（固有名詞・行動・感情・トピック等）
4. 返却値に応じて `emotions` を加減算（0-100 にクランプ）。判定基準が甘くなりがちで常に上がり続けるのを防ぐため、中位の選択肢を 0、下位の選択肢を `-delta` に割り当てている：
   - すごく嬉しい / すごく上がる / すごく安心 → `+delta`
   - 嬉しい / 上がる / 安心 → 変化なし（0）
   - 普通 / 上がらない / 普通 → `-delta`
5. **長期記憶の想起**：抽出した `keywords` を使って `recollections` テーブルを置き換える：
   - `long_term_keywords` から `character_id` 一致・`keyword IN (抽出語...)` の行を `mid_term_memory_id` で集約（`impression` は `MAX` を取る）
   - `impression` 降順で上位 20 件を取り出し、その中から **ランダムに最大 5 件** サンプリング（候補が 5 件未満ならその全件）
   - `recollections` の同 `character_id` 行を一旦全削除してから、選んだ `mid_term_memory_id` を全件挿入する（原子的に上書き）
   - `keywords` が空ならスキップ（前回の `recollections` を保持）
6. 60 秒待ってループ先頭へ戻る

好感度（affection）も同様の3段階で判定し、`すごく上がる` → `+delta`、`上がる` → 0、`上がらない` → `-delta` を `emotions.affection` に反映する。

## API一覧

### GET /api/character  （キャラクター情報取得API）
- 現在のキャラクターの `id` / `name` / `voice_id` を返す。
- レスポンス: `{"id": "<char_id>", "name": "<表示名>", "voice_id": "<ElevenLabsボイスID>"}`
- フロントエンドは起動時にこれを取得し、キャラ画像のパス（`images/<id>/stand.png` 等）と TTS のボイスIDに使用する。

### GET /api/messages/next  （AI会話取得API）
- DB から `speaker="ai"` かつ未再生フラグ=未再生 の最古レコードを1件返す
- 返却と同時に、そのレコードの未再生フラグを「再生済」に更新（取得時即マーク方式）
- 未再生メッセージがない場合は空レスポンス
- 取りこぼし許容（エラー多発時に2段階フラグ化を再検討）

### GET /api/mission  （デイミッション取得API）
- 現キャラ・現在の Day に紐づく `missions.content` を返す（未設定なら空文字）
- レスポンス: `{"day": <int>, "content": <str>}`

### PUT /api/mission  （デイミッション更新API）
- リクエスト: `{"content": "<本文>"}`
- 現キャラ・現在の Day で upsert（`ON CONFLICT(character_id, day) DO UPDATE`）
- 本文が空文字（前後空白除去後）の場合は該当行を **DELETE**（未設定状態へ戻す）
- レスポンス: `{"day": <int>, "content": <保存後の文字列>}`

### POST /api/messages/player  （プレイヤー会話保存API）
- フロントから送られたテキスト（手入力 or 音声入力）をDBに保存
- speaker="player"、未再生フラグは不要（または常に再生済扱い）

### 静的ファイル配信
- `app.mount("/", StaticFiles(directory="frontend", html=True))`
- index.html およびキャラ画像等の静的アセットを uvicorn から配信

## DBスキーマ（SQLite3）

### `messages` テーブル（会話履歴 = 短期記憶）
- `id` INTEGER PRIMARY KEY AUTOINCREMENT
- `speaker` TEXT NOT NULL  -- "ai" または "player"
- `content` TEXT NOT NULL
- `played` INTEGER NOT NULL DEFAULT 0  -- 0=未再生, 1=再生済
- `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
- `character_id` TEXT  -- A-Za-z 1-16文字。起動時に既存 NULL 行は config の current_id で backfill
- `day` INTEGER  -- プレイ日。挿入時は `app.state.current_day` を書き込む。既存 NULL 行は 1 で backfill
- INDEX `idx_messages_char_id` (`character_id`, `id` DESC)
- INDEX `idx_messages_char_day` (`character_id`, `day`, `id` DESC)

### `missions` テーブル（デイミッション = その日のプレイ目標）
- `id` INTEGER PRIMARY KEY AUTOINCREMENT
- `character_id` TEXT NOT NULL
- `day` INTEGER NOT NULL
- `content` TEXT NOT NULL  -- ミッション本文（プレイヤーが入力）
- `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
- `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
- UNIQUE (`character_id`, `day`)  -- 1日1キャラあたり1件
- INDEX `idx_missions_char_day` (`character_id`, `day`)

### `mid_term_memories` テーブル（中期記憶 = 直近会話の要約）
- `id` INTEGER PRIMARY KEY AUTOINCREMENT
- `character_id` TEXT NOT NULL
- `summary` TEXT NOT NULL  -- 要約本文（既定 100文字程度）
- `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
- `day` INTEGER  -- 要約を作った時点のプレイ日。既存 NULL 行は 1 で backfill
- `last_message_id` INTEGER  -- この要約に取り込んだ `messages.id` の最大値（次回バッチ判定の基準）。既存 NULL 行は 0 で backfill
- `game_name` TEXT  -- 要約を作った時点の `config.yaml` の `game.name`。長期記憶バッチで使用。既存 NULL 行は backfill しない（読み出し時に現行 `game.name` をフォールバックとして利用）
- INDEX `idx_mid_term_char` (`character_id`, `id` DESC)
- INDEX `idx_mid_term_char_day` (`character_id`, `day`, `id` DESC)

### `long_term_keywords` テーブル（長期記憶 = 中期記憶への検索キーワード）
- `id` INTEGER PRIMARY KEY AUTOINCREMENT
- `character_id` TEXT NOT NULL  -- どのキャラの記憶か
- `keyword` TEXT NOT NULL  -- 関連づけるキーワード（短い単語句）。1つの中期記憶に対し複数行入る
- `mid_term_memory_id` INTEGER NOT NULL  -- 紐づく `mid_term_memories.id`
- `impression` INTEGER NOT NULL  -- 印象度 1（薄い）〜 5（強い）。同じ中期記憶に紐づく全行で同値
- `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
- INDEX `idx_long_term_char_keyword` (`character_id`, `keyword`)
- INDEX `idx_long_term_mid_term` (`mid_term_memory_id`)

### `recollections` テーブル（回想 = 現在キャラが想起中の長期記憶）
- `character_id` TEXT NOT NULL  -- どのキャラの回想か
- `mid_term_memory_id` INTEGER NOT NULL  -- 想起対象の `mid_term_memories.id`
- INDEX `idx_recollections_char` (`character_id`)
- 感情ループの「キーワード抽出 → `long_term_keywords` 照合 → 上位20件からランダム5件サンプリング」で 60 秒間隔に全件上書きされる
- 会話生成プロンプトはここに行があるキャラについて、対応する `mid_term_memories` を1行ずつ `ゲーム名：{game_name} 出来事：{summary}` 形式で「関連した過去の出来事」セクションに注入する

### `characters` テーブル（キャラクターマスター = 感情の変化差分）
- `character_id` TEXT PRIMARY KEY  -- A-Za-z 1-16文字
- `happy_delta` INTEGER NOT NULL DEFAULT 5  -- 嬉しさの変化差分
- `tension_delta` INTEGER NOT NULL DEFAULT 5  -- テンションの変化差分
- `safe_delta` INTEGER NOT NULL DEFAULT 5  -- 安心の変化差分
- `affection_delta` INTEGER NOT NULL DEFAULT 5  -- プレイヤーへの好感度の変化差分
- 起動時に `hina` レコードを `INSERT OR IGNORE` で挿入（全 delta = 5）
- 同様に `config.character.current_id` のレコードも `INSERT OR IGNORE` で初期化

### `emotions` テーブル（キャラごとの現在の感情値）
- `character_id` TEXT PRIMARY KEY
- `happy` INTEGER NOT NULL DEFAULT 50  -- 0-100、クランプ運用
- `tension` INTEGER NOT NULL DEFAULT 50
- `safe` INTEGER NOT NULL DEFAULT 50
- `affection` INTEGER NOT NULL DEFAULT 50
- 起動時に `config.character.current_id` のレコードを `INSERT OR IGNORE` で初期化（全値 50）
- 更新は `apply_emotion_delta` で行い、`MAX(0, MIN(100, value + delta))` でクランプ

### マイグレーション方針
- 起動時 `init_db` で `PRAGMA table_info(<table>)` を見て、不足カラム（`character_id`, `day`, `last_message_id`, `game_name`）があれば `ALTER TABLE ADD COLUMN` で追加
- 既存行は以下の規則で backfill：
  - `messages.character_id IS NULL` → `config.character.current_id`
  - `messages.day IS NULL` → `1`
  - `mid_term_memories.day IS NULL` → `1`
  - `mid_term_memories.last_message_id IS NULL` → `0`
  - `mid_term_memories.game_name` … **backfill しない**（NULL のまま残す。長期記憶バッチは読み出し時に現行 `config.yaml` の `game.name` をフォールバックとして使う）
- `characters` / `emotions` / `long_term_keywords` / `recollections` テーブルは `CREATE TABLE IF NOT EXISTS` で初回作成（`characters` / `emotions` は `INSERT OR IGNORE` で初期行を投入。`long_term_keywords` / `recollections` は初期行なし）
- 上記は冪等で、再起動を繰り返しても副作用なし

# フロントエンド仕様

## 画面構成
画面は縦長（左 2/3 が ゲーム画面、右 1/3 にブラウザを縦長表示する想定）。
上から順に：
1. デイミッション欄（`現在のミッション：` ラベル＋本文表示＋[編集]ボタン）
2. キャラ画像
3. AI のメッセージ表示吹き出し
4. プレイヤーのメッセージ入力欄（テキスト入力 + 音声入力ボタン + 音声入力AUTOボタン）

### デイミッションUI挙動
- 起動時に `GET /api/mission` を呼んで現 Day のミッションを表示。未設定なら `（未設定）` 表示
- 「編集」クリックで **モーダルポップアップ** が開き、input ＋[保存]/[キャンセル] が表示される
- Enter で保存、Esc またはモーダル外側クリックでキャンセル
- 「保存」で `PUT /api/mission` を呼び、空文字保存なら未設定状態へ戻る
- **ハンズフリー編集**：プレイヤー入力（音声・テキスト共通）が `ミッション` で始まる場合、続く本文を `PUT /api/mission` に流して即更新する（このときの入力は会話履歴には残さない）
  - `ミッション` 直後の空白・`、。：:,.` は区切り文字として吸収する
  - 残り本文が空（`ミッション` のみ）の入力は誤検知防止で無視する

## メッセージ受信ループ
起動と同時に以下のループを開始：

1. `/api/messages/next` にアクセス
2. 新着メッセージなし → 5秒待ってループ先頭へ
3. 新着メッセージあり → ElevenLabs API で TTS 音声を合成・取得
4. 音声取得完了後：
   - **テキスト表示と音声再生を同時に開始**
   - テキストはタイプライター風に **7文字/s** で1文字ずつ表示
   - 末尾でテキストと音声がズレるのは許容（7文字/s は検証済みの最適値）
5. テキスト表示と音声再生の両方が終わったらループ先頭へ
   - このタイミングで「音声入力AUTO」が ON ならマイク入力を自動開始

## プレイヤー入力
- **テキスト手入力**：エンター送信 → `/api/messages/player` に POST
- **音声入力ボタン**：押すと Web Speech API で録音開始、認識結果を `/api/messages/player` に POST
- **音声入力AUTOボタン**：トグル ON 時、上記メッセージ受信ループで再生終了タイミングごとに自動でマイク ON
  - エコー対策はヘッドセット使用前提で運用（必要に応じてガード時間を入れる）

# 配布・運用

## ZIPでの配布
- `hinalive/` フォルダ全体を ZIP にまとめて転送
- API キー類は `.env` ではなく `config.yaml` / `config.js` で持つ
  （自分専用のため簡略化）
- 稼働PC側で実施：
  1. ZIP展開
  2. `setup.ps1` を実行（venv 作成 + `pip install -r requirements.txt`）
  3. `config.yaml` の `capture.window_title` を、プレイするゲームウィンドウのタイトル部分一致文字列に設定
  4. `config.yaml` の `prompts.task_path` を、プレイするタイトル用のプロンプトファイルに設定
  5. uvicorn 起動 → ブラウザで `http://localhost:8000` を開く

## ゲームタイトルを切り替えるとき
1. `backend/prompts/` 配下に該当タイトル用の `.md` を用意（既存ファイルを流用または新規作成）
2. `config.yaml` の `prompts.task_path` を新しいファイルに書き換える
3. `config.yaml` の `capture.window_title` を新ゲームのウィンドウタイトルに合わせる
4. uvicorn を再起動

ワンショットで切り替えたい場合は、`config.yaml` を書き換えず以下のように起動時オプションで指定できる：

```powershell
python backend\main.py --window "Minecraft" --cheer minecraft
```

## 開発環境について
開発PCとテストPC（実稼働PC）は分かれている。
ZIPでまとめて転送できるよう、必要なファイルを `hinalive/` 1フォルダに収める。

# 確定事項サマリ

| 項目 | 決定内容 |
|---|---|
| コンセプト | 実況＋応援の相棒型（汎用、タイトル非依存） |
| ゲーム切替方式 | プロンプトファイルを差し替え（手動。`config.yaml` で active を指定） |
| Geminiモデル | `gemini-3.1-pro-preview`,`gemini-3.5-flash` |
| Pythonバージョン | 3.13 |
| ElevenLabsプラン | Creator で開始、不足時 Pro へ |
| キャプチャ方式 | Python ネイティブ（`mss` + `pygetwindow`、ウィンドウタイトル指定）。加工後JPGのみ保存 |
| キャラクター管理 | `character.current_id`（A-Za-z 1-16文字、`--char_id` で上書き可）で識別。`backend/characters/<id>/` にプロンプト・setting.yaml、`frontend/images/<id>/` に立ち絵を配置。会話履歴・記憶テーブルもこのIDで分離 |
| Day（プレイ日） | 整数。`--day` で指定、未指定なら現キャラの最新履歴の day を継続、無ければ 1。`messages` / `mid_term_memories` の挿入時に必ず書き込み、生成プロンプトの履歴取得は現 Day で絞り込み |
| デイミッション | `missions` テーブル（`character_id` × `day` で1件）。フロントから GET/PUT で編集。生成プロンプトにも注入 |
| 記憶レイヤー | 短期=`messages`（生ログ）／中期=`mid_term_memories`（現 Day の直近30件を100文字要約、現 Day で新規20件溜まったら追加。`game_name` カラムを持つ）／長期=`long_term_keywords`（中期記憶にキーワード＋印象度1-5を紐付け。Day 終了後に CLI `--longterm-batch` で生成）／回想=`recollections`（感情ループでキーワード抽出 → 長期記憶上位20件からランダム5件を選んで上書き。生成プロンプトに「関連した過去の出来事」として注入） |
| 要約モデル | 中期記憶バッチ・長期記憶バッチとも常に flash 固定 |
| 感情値 | `emotions`（happy/tension/safe/affection、0-100、default 50）。差分は `characters` マスター（hina 初期値=各 5）。happy/tension/safe は 60 秒間隔の感情ループで判定、affection は中期記憶挿入直後に判定。判定モデルは flash 固定で JSON 強制出力 |
| テキスト/音声同期 | 同時開始のみ、末尾ズレ許容（7文字/s 固定） |
| 再生済フラグ | 取得時に即立てる（取りこぼし許容） |
| 画像添付 | 最新キャプチャ1枚のみ |
| 会話履歴 | テキストのみ、`話者:内容` 形式、直近30件 |
| 外部設定 | クリップ座標・プロンプト・Gemini APIキー を外部ファイル化 |
| ElevenLabs APIキー | フロント直書き（自分専用のため許容） |
| メインループ実装 | asyncio バックグラウンドタスクで実装（API ブロック回避） |
