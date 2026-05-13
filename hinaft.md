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
- フリーソフト **ShareX** がインストールされている。プレイ中のゲーム画面を定期的にキャプチャして、所定フォルダに**固定ファイル名**で保存する
- ブラウザは Google Chrome Desktop
- ゲームは特定タイトルに依存しない（ShareX でキャプチャ可能なものであれば何でも）

# 技術スタック

## バックエンド
- Python 3.13
- FastAPI
- uvicorn（ローカルサーバ、ポート 8000、http）
- SQLite3
- 使用AIモデル：`gemini-3.1-pro-preview`,`gemini-3-flash-preview` ※切り替え可能

## フロントエンド
- HTML / CSS / JavaScript（素のJS）
- Web Speech API（音声入力）
- ElevenLabs API（TTS）

# フォルダ構成

```
hinaft/
├── backend/
│   ├── main.py
│   ├── config.yaml          # クリップ座標、Gemini APIキー、モデル名、active プロンプト指定 等
│   ├── prompts/
│   │   ├── character.md     # キャラ設定プロンプト（常時ロード）
│   │   ├── zwift.md         # ゲーム別の実況・応援指示（差し替え対象）
│   │   ├── minecraft.md     # 〃
│   │   └── ...              # 必要に応じてタイトル別ファイルを追加
│   ├── requirements.txt
│   └── data.db              # SQLite
├── frontend/
│   ├── index.html
│   ├── app.js
│   ├── config.js            # ElevenLabs APIキー等（フロント設定）
│   ├── style.css
│   └── images/
│       ├── stand.png        # キャラ立ち絵（APNG）
│       └── talk.png         # キャラ会話絵（APNG）
├── captures/
│   ├── (ShareX保存先：固定ファイル名で上書き保存)
│   └── processing/          # rename後の作業用フォルダ
└── setup.ps1                # 初期設定スクリプト（PowerShell用、venv作成 + pip install）
```

# 設定ファイル仕様

## backend/config.yaml
- Gemini APIキー
- 使用モデル名（`gemini-3.1-pro-preview` / `gemini-3-flash-preview`）と起動時デフォルト
- ShareX キャプチャ保存パス（固定ファイル名のフルパス）
- rename 作業フォルダのパス
- rename 失敗時のリトライ間隔・上限回数
- キャプチャ画像のクリップ座標（左上 x1, y1 / 右下 x2, y2）
- リサイズ後の解像度（横幅 px。縦は成り行き）
- 会話履歴の取得件数（既定 30件）
- メインループ間隔（既定 10秒）
- DBファイルパス
- プロンプトファイルパス
  - `prompts.character_path` … キャラ設定（常時ロード）
  - `prompts.task_path` … active な実況応援プロンプト（タイトル切り替え時はここを書き換える）

## frontend/config.js
- ElevenLabs APIキー
- ElevenLabs ボイスID 等

※ ElevenLabs APIキーをフロントに置くのは本来危険だが、自分専用ソフトのため許容。
　将来的にバックエンド経由でプロキシする実装に切り替えることも可能。

## プロンプトファイル

- `backend/prompts/character.md` … キャラ設定（口調・性格・世界観など）。**常にロード**される。
- `backend/prompts/<game>.md` … タイトル別の実況・応援指示。「画面から何を読み取るか」「どんな声かけをするか」「禁止事項」などを記述する。
  - 例：`zwift.md`, `minecraft.md`, など必要に応じて追加
  - active なものを `config.yaml` で1つ指定する

# バックエンド仕様

## メインループ処理
FastAPI 起動時に asyncio のバックグラウンドタスクとして以下のループを起動する
（API リクエスト処理をブロックしないため、必ず非同期で実装する）。

1. ShareX が保存したキャプチャファイル（固定ファイル名）の存在をチェック
2. 存在しない → 一定秒数待ってループ先頭へ
3. 存在する → ファイルを `captures/processing/` 配下にユニークな名前で **rename**
   - rename が失敗した場合（ShareX が書き込み中など）は数秒待ってリトライ
   - リトライ上限（例：5回）を超えたら警告ログを出してループ先頭へ
4. rename 成功したファイルをクリップ・リサイズして別名で保存。元画像は削除
5. DBから過去の会話履歴を直近30件取得
6. プロンプト構築：
   - キャラ設定プロンプト（`character.md`）
   - 実況応援プロンプト（`config.yaml` で指定された active な game プロンプト）
   - 会話履歴（テキストのみ、`AI: ～` / `プレイヤー: ～` の繰り返し形式）
   - 最新キャプチャ画像1枚を添付（履歴に画像は含めない）
7. Gemini API に投げて、実況・応援メッセージを生成
8. 生成されたメッセージをDBに保存（speaker="ai"、未再生フラグ=未再生）
9. 5秒おきに次のユーザーメッセージを待つ。次のメッセージが来るか、所定秒数繰り返して、メッセージが来なければループ先頭へ戻る

## API一覧

### GET /api/messages/next  （AI会話取得API）
- DB から `speaker="ai"` かつ未再生フラグ=未再生 の最古レコードを1件返す
- 返却と同時に、そのレコードの未再生フラグを「再生済」に更新（取得時即マーク方式）
- 未再生メッセージがない場合は空レスポンス
- 取りこぼし許容（エラー多発時に2段階フラグ化を再検討）

### POST /api/messages/player  （プレイヤー会話保存API）
- フロントから送られたテキスト（手入力 or 音声入力）をDBに保存
- speaker="player"、未再生フラグは不要（または常に再生済扱い）

### 静的ファイル配信
- `app.mount("/", StaticFiles(directory="frontend", html=True))`
- index.html およびキャラ画像等の静的アセットを uvicorn から配信

## DBスキーマ（SQLite3）

`messages` テーブル
- `id` INTEGER PRIMARY KEY AUTOINCREMENT
- `speaker` TEXT NOT NULL  -- "ai" または "player"
- `content` TEXT NOT NULL
- `played` INTEGER NOT NULL DEFAULT 0  -- 0=未再生, 1=再生済
- `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP

# フロントエンド仕様

## 画面構成
画面は縦長（左 2/3 が ゲーム画面、右 1/3 にブラウザを縦長表示する想定）。
上から順に：
1. キャラ画像
2. AI のメッセージ表示吹き出し
3. プレイヤーのメッセージ入力欄（テキスト入力 + 音声入力ボタン + 音声入力AUTOボタン）

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
- `hinaft/` フォルダ全体を ZIP にまとめて転送
- API キー類は `.env` ではなく `config.yaml` / `config.js` で持つ
  （自分専用のため簡略化）
- 稼働PC側で実施：
  1. ZIP展開
  2. `setup.ps1` を実行（venv 作成 + `pip install -r requirements.txt`）
  3. ShareX のキャプチャ保存先を `hinaft/captures/` に設定
  4. ShareX のキャプチャ周期と保存ファイル名を設定（固定ファイル名）
  5. `config.yaml` の `prompts.task_path` を、プレイするタイトル用のプロンプトファイルに設定
  6. uvicorn 起動 → ブラウザで `http://localhost:8000` を開く

## ゲームタイトルを切り替えるとき
1. `backend/prompts/` 配下に該当タイトル用の `.md` を用意（既存ファイルを流用または新規作成）
2. `config.yaml` の `prompts.task_path` を新しいファイルに書き換える
3. uvicorn を再起動

## 開発環境について
開発PCとテストPC（実稼働PC）は分かれている。
ZIPでまとめて転送できるよう、必要なファイルを `hinaft/` 1フォルダに収める。

# 確定事項サマリ

| 項目 | 決定内容 |
|---|---|
| コンセプト | 実況＋応援の相棒型（汎用、タイトル非依存） |
| ゲーム切替方式 | プロンプトファイルを差し替え（手動。`config.yaml` で active を指定） |
| Geminiモデル | `gemini-3.1-pro-preview`,`gemini-3-flash-preview` |
| Pythonバージョン | 3.13 |
| ElevenLabsプラン | Creator で開始、不足時 Pro へ |
| キャプチャ受け渡し | 固定ファイル名 → rename 移動（失敗時リトライ） |
| テキスト/音声同期 | 同時開始のみ、末尾ズレ許容（7文字/s 固定） |
| 再生済フラグ | 取得時に即立てる（取りこぼし許容） |
| 画像添付 | 最新キャプチャ1枚のみ |
| 会話履歴 | テキストのみ、`話者:内容` 形式、直近30件 |
| 外部設定 | クリップ座標・プロンプト・Gemini APIキー を外部ファイル化 |
| ElevenLabs APIキー | フロント直書き（自分専用のため許容） |
| メインループ実装 | asyncio バックグラウンドタスクで実装（API ブロック回避） |
