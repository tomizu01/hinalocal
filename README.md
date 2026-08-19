# Hinalocal

任意のPCゲームをプレイ中に、キャラが画面の状況を**実況**したり**応援**したりしてくれる
汎用ゲーム実況支援AIコンパニオンです（自宅・個人利用向け）。

LLM・TTS ともに外部APIを使わず、**ローカルネットワーク内の Ollama と AivisSpeech Engine** で動きます。

詳細仕様は `hinalocal.md` を参照してください。

# 必要環境・ソフト

## ゲームPC（本アプリを動かす側）
- Windows 11 64bit
- Chrome Desktop ブラウザ
- マイク・ヘッドホン推奨
- Python 3.13

## 推論PC（LLM / TTS を動かす側。ゲームPCと同一でも可）
- [Ollama](https://ollama.com/)（vision 対応モデル。既定は `gemma4:26b`）
- [AivisSpeech](https://aivis-project.com/)（AivisSpeech Engine を含む）

特定のゲームタイトルへの依存はありません。Windows 上でウィンドウとして表示されるゲームであれば動作します
（画面キャプチャは Python の `mss` + `pygetwindow` で行うため、別途キャプチャソフトのインストールは不要）。

# 事前準備（推論PC側）

Ollama
- インストール後、LAN からの接続を許可するため環境変数 `OLLAMA_HOST=0.0.0.0` を設定して再起動する
- モデルを取得する：`ollama pull gemma4:26b`
  - 画面キャプチャを送るため **vision 対応モデルが必須**
  - `gemma4:26b` は実測で 17GB 前後（`ollama ps` の表示は 18GB）。VRAM 16GB の GPU では
    収まりきらず一部が CPU 実行になる（それでも 1発話 3〜4秒程度で動く）
  - 完全に GPU に載せたい場合は、より小さい vision 対応タグ（`gemma4:12b` = 7.6GB 等）を
    `llm.model` に指定する。画像の読み取り精度は多少落ちる
- 疎通確認（ゲームPCから）：`curl http://<推論PCのIP>:11434/api/tags`

AivisSpeech
- インストール後、使いたい音声モデル（話者）を追加する
- LAN からの接続を許可するため、Engine を `--host 0.0.0.0 --port 10101` で起動する
- 疎通確認（ゲームPCから）：`curl http://<推論PCのIP>:10101/speakers`

# Setup手順

必要ソフトをインストールする
- Python 3.13
- Chrome Desktop

ソースを展開する

設定ファイルを用意する（サンプルからコピーして編集）
- `backend\config.yaml.example` を `backend\config.yaml` にコピー
- `frontend\config.js.example` を `frontend\config.js` にコピー
  - これら実体ファイルは環境ごとの設定（接続先IP等）を含むため `.gitignore` で除外されている

下記設定ファイルを書き換える
- `backend\config.yaml`
  - `llm.base_url` に Ollama の接続先（例 `http://192.168.1.20:11434` / `http://ollama-pc.local:11434`）
  - `llm.model` に使うモデル（既定 `gemma4:26b`）
  - `tts.base_url` に AivisSpeech Engine の接続先（例 `http://192.168.1.20:10101`）
  - `tts.default_style_id` に既定のスタイルID
    （起動後に `http://localhost:8000/api/tts/speakers` を開くと一覧が確認できる）
  - `capture.window_title` にプレイするゲームのウィンドウタイトル（部分一致）を指定
  - `image.clip` でウィンドウから切り出す範囲を指定（任意）
- `backend\characters\<char_id>\setting.yaml`
  - `style_id` にそのキャラに使う AivisSpeech のスタイルID（未設定なら `tts.default_style_id`）
  - 必要なら `tts:` で話速・抑揚などをキャラ別に上書き
- `frontend\config.js`
  - 表示速度やポーリング間隔の調整（API キーは不要）
- `frontend\images`
  - キャラ画像を `stand.png` / `talk.png` として配置する（APNG推奨）
  - これらは大容量のためリポジトリには含まれていない（`.gitignore` で除外）
- `backend\characters\<char_id>\prompts\`
  - `character.md` でキャラの口調・性格・世界観を定義
  - `cheer.md` に既定の実況・応援の指示内容を記述する
  - タイトルごとに `<game>.md`（例：`zwift.md`, `minecraft.md`）を用意し、`--cheer <名前>` で切り替える
  - なお `backend\prompts\summary.md` は中期記憶の要約テンプレート（キャラ非依存）

Windows PowerShell を起動し、ソースを展開したディレクトリに移動して初期設定スクリプトを実行する
- `.\setup.ps1`
- スクリプトが実行できない場合は下記コマンドを実行する
  - `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser`

Python を実行
- `.\backend\venv\Scripts\Activate.ps1`
- `python backend\main.py`
  - ワンショットで対象を変えたい場合は `python backend\main.py --window "Minecraft" --cheer minecraft` のように起動時オプションでも上書き可能

ブラウザでアクセス
- `http://localhost:8000`

# ゲームタイトルを切り替えるとき

1. `backend\characters\<char_id>\prompts\` 配下に該当タイトル用の `.md` を用意（既存ファイルを流用または新規作成）
2. `backend\config.yaml` の `capture.window_title` を新ゲームのウィンドウタイトルに合わせる
3. `backend\config.yaml` の `game.name` を新ゲームの表示名に合わせる
4. キャプチャ範囲（`image.clip`）も必要に応じて調整する
5. 用意した `.md` を `--cheer` で指定して uvicorn を再起動

（`config.yaml` を書き換えず起動時オプションだけで切り替えることもできる）

```powershell
python backend\main.py --char_id hina --window "Zwift" --game "Zwift" --cheer zwift
```

# うまく動かないとき

**キャラがしゃべらない / ログに `LLM空応答 ... done_reason=length`**
`config.yaml` の `llm.think` が `false` になっているか確認する。`gemma4` 系のような
thinking 対応モデルは、`think: false` を送らないと生成がすべて推論トレース側に流れ、
発話本文が空になる。

**発話が遅い（初回に数十秒かかる）**
モデルのロード時間。`llm.keep_alive` の間は常駐するので2回目以降は数秒になる。
毎回遅い場合は `ollama ps` で `CPU/GPU` の分割を確認する。CPU 側の割合が大きいなら
モデルが VRAM に収まっていないので、より小さいモデル／量子化を選ぶ。

**TTS が鳴らない（ログに `tts unreachable`）**
AivisSpeech Engine が起動しているか、`tts.base_url` が正しいかを確認する。
別PCの場合は Engine を `--host 0.0.0.0` で起動する必要がある。
`http://localhost:8000/api/tts/speakers` が一覧を返せば疎通OK。

---

※このソフトウェアは、自宅での個人利用向けに設計されているため、
　絶対に外部向けのサーバで公開しないでください。ローカルネットワーク内でのみ
　実行してください。認証機構を一切持たないため、外部に公開すると
　誰でも会話履歴の読み書きやサーバ停止ができてしまいます。

# 注意事項

※ Hinalocal は個人開発による非公式ツールです。
ご利用は自己責任でお願いいたします。

本システムの利用により発生した問題について、開発者は責任を負いません。
また、本ソフトが対応する各ゲームタイトルの提供元・運営とは一切関係ありません。
不具合や質問について、各ゲームタイトルの提供元へのお問い合わせは行わないようお願いいたします。

Hinalocal is an unofficial personal project.
Use at your own risk.

The developer is not responsible for any issues caused by the use of this system.
Hinalocal is not affiliated with the publishers of any game it is used with.
Please do not contact those publishers regarding any issues, questions, or support related to this system.
