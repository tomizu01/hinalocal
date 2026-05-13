# Hinalive

任意のPCゲームをプレイ中に、キャラが画面の状況を**実況**したり**応援**したりしてくれる
汎用ゲーム実況支援AIコンパニオンです（自宅・個人利用向け）。

詳細仕様は `hinalive.md` を参照してください。

# 必要環境・ソフト

- Windows 11 64bit
- Chrome Desktop ブラウザ
- マイク・ヘッドホン推奨
- Python 3.13

特定のゲームタイトルへの依存はありません。Windows 上でウィンドウとして表示されるゲームであれば動作します
（画面キャプチャは Python の `mss` + `pygetwindow` で行うため、別途キャプチャソフトのインストールは不要）。

# 事前準備

- Gemini API Key を取得する
- ElevenLabs API Key を取得する

# Setup手順

必要ソフトをインストールする
- Python 3.13
- Chrome Desktop

ソースを展開する

設定ファイルを用意する（サンプルからコピーして編集）
- `backend\config.yaml.example` を `backend\config.yaml` にコピー
- `frontend\config.js.example` を `frontend\config.js` にコピー
  - これら実体ファイルは API キーを含むため `.gitignore` で除外されている

下記設定ファイルを書き換える
- `backend\config.yaml`
  - Gemini API Key の埋め込み
  - `capture.window_title` にプレイするゲームのウィンドウタイトル（部分一致）を指定
  - `image.clip` でウィンドウから切り出す範囲を指定（任意）
  - `prompts.task_path` にプレイするタイトル用のプロンプトファイルを指定
- `frontend\config.js`
  - ElevenLabs の API キー・ボイスID の埋め込み
- `frontend\images`
  - キャラ画像を `stand.png` / `talk.png` として配置する（APNG推奨）
  - これらは大容量のためリポジトリには含まれていない（`.gitignore` で除外）
- `backend\prompts\`
  - `character.md` でキャラの口調・性格・世界観を定義
  - タイトルごとに `<game>.md`（例：`zwift.md`, `minecraft.md`）を用意し、
    実況・応援の指示内容を記述する

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

1. `backend\prompts\` 配下に該当タイトル用の `.md` を用意（既存ファイルを流用または新規作成）
2. `backend\config.yaml` の `prompts.task_path` を新しいファイルに書き換える
3. `backend\config.yaml` の `capture.window_title` を新ゲームのウィンドウタイトルに合わせる
4. キャプチャ範囲（`image.clip`）も必要に応じて調整する
5. uvicorn を再起動

（その都度切り替える場合は `--window` / `--cheer` 起動オプションでも可）

---

※このソフトウェアは、自宅への個人利用向けに設計されているため、
　絶対に外部向けのサーバで公開しないでください。localhost でのみ
　実行してください。API キーの一部はブラウザとの通信に含まれ、
　API キーの不正利用を招く恐れがあります。

# 注意事項

※ Hinalive は個人開発による非公式ツールです。
ご利用は自己責任でお願いいたします。

本システムの利用により発生した問題について、開発者は責任を負いません。
また、本ソフトが対応する各ゲームタイトルの提供元・運営とは一切関係ありません。
不具合や質問について、各ゲームタイトルの提供元へのお問い合わせは行わないようお願いいたします。

Hinalive is an unofficial personal project.
Use at your own risk.

The developer is not responsible for any issues caused by the use of this system.
Hinalive is not affiliated with the publishers of any game it is used with.
Please do not contact those publishers regarding any issues, questions, or support related to this system.
