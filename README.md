# Hinalocal

任意のPCゲームをプレイ中に、キャラが画面の状況を**実況**したり**応援**したりしてくれる
汎用ゲーム実況支援AIコンパニオンです（自宅・個人利用向け）。

LLM・TTS ともに外部APIを使わず、**ローカルネットワーク内の Ollama と AivisSpeech Engine** で動きます。
音声入力（STT）も **ゲームPC上の faster-whisper** で処理するため、インターネットに繋がっていなくても動きます。

詳細仕様は `hinalocal.md` を参照してください。

# 必要環境・ソフト

## ゲームPC（本アプリを動かす側）
- Windows 11 64bit
- Chrome Desktop ブラウザ
- マイク・ヘッドホン推奨
- Python 3.13
- NVIDIA GPU（音声認識用に VRAM 2GB 以上の空き。LLM / TTS の使用分は別勘定）
  - **NVIDIA ドライバは R525 以降**（2022年11月以降のもの）。これより古いと
    音声認識モデルのロード時にエラーではなくプロセスごとクラッシュする
  - CUDA Toolkit の別途インストールは不要（cuBLAS / cuDNN は `pip install` で venv 内に入る）
  - GPU が無い / VRAM が足りない場合は `backend\config.yaml` の `stt.device` を `"cpu"` にするか、
    `stt.model` を `medium` / `small` に落とす

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
  - `stt.model_dir` に音声認識モデルの置き場所（既定 `C:/hinalocal/backend/models/large-v3-turbo`）
    - 展開先を変えた場合はここも合わせて書き換える
  - `stt.initial_prompt` は音声認識のヒント（固有名詞を先に知らせると精度が上がる）
    - `{char_names}` にキャラ名、`{game_name}` にゲーム名が起動時に自動で入る
    - タイトル固有の用語（アイテム名・地名など）を書き足すとさらに効く
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
  - venv 作成 → `pip install` → **音声認識モデル（約1.6GB）のダウンロード** まで行う
  - モデルの取得にはインターネット接続が必要（**取得後は不要**。以後オフラインで動く）
  - モデルだけ入れ直したいときは `python backend\download_stt_model.py`
    （`--model medium` のようにモデルを変更、`--list` で一覧表示）
- スクリプトが実行できない場合は下記コマンドを実行する
  - `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser`

Python を実行
- `.\backend\venv\Scripts\Activate.ps1`
- 音声認識（STT）が動く状態かを確認する（推奨）
  - `python backend\check_stt.py`
  - ドライバ・CUDAライブラリ・モデル・ロードまでを段階ごとに確認し、
    最後に `すべて成功しました` と出れば OK
  - 失敗した場合は、どの設定なら動くか（`float16` / `cpu` 等）まで判定して表示する
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

**マイクボタンが「準備中...」のまま押せない**
音声認識モデルのロード中（数秒〜十数秒）。それ以上待っても変わらない場合はサーバのログを見る。
`STT モデルが見つかりません` なら `python backend\download_stt_model.py` でモデルを取得し、
`backend\config.yaml` の `stt.model_dir` が実際の置き場所と一致しているか確認する。

**ログに `Library cublas64_12.dll is not found or cannot be loaded`**
GPU 実行に必要な CUDA ライブラリ（pip パッケージ）が入っていない。venv を有効化して
`pip install -r backend
equirements.txt` を実行し直す。
`nvidia-cublas-cu12` は 553MB あり、ダウンロードに失敗していても
`setup.ps1` のログを見落としがち。`pip list` に `nvidia-cublas-cu12` と
`nvidia-cudnn-cu12` があるか確認する。

**起動直後に無言でプロセスが終了する（トレースバックも出ない）**
CUDA / cuDNN 側のネイティブクラッシュ。まず NVIDIA ドライバを最新にする
（R525 以降が必要。古いドライバではエラーではなくプロセスごと落ちる）。
`python backend\check_stt.py` を実行すると、ドライバ・DLL・モデル・ロードを
段階ごとに確認し、どの設定なら動くかまで切り分けてくれる。

**音声入力が使えない / ログに `GPU ロードに失敗`**
`nvidia-smi` で GPU とドライバを確認する。VRAM が足りない場合は `stt.model` を
`medium` / `small` に落とすか、`stt.device` を `"cpu"` にする（CPU では1発話に数秒〜十数秒かかる）。
CPU でも遅すぎる場合は `stt.enabled: false` にすればテキスト入力だけで運用できる。

**喋っていないのに「ご視聴ありがとうございました」等が入力される**
Whisper が無音・環境音に対して出す典型的な幻聴。既定でフィルタしているが、
別のパターンが出たら `backend\config.yaml` の `stt.hallucination_blocklist` に追加する。
そもそも拾いにくくするには `frontend\config.js` の `stt.vadMinRms` を上げる。

**録音がなかなか終わらない / すぐ切れる**
`frontend\config.js` の `stt.silenceEndMs`（無音何msで打ち切るか）と
`stt.vadNoiseMultiplier` / `stt.vadMinRms`（どの音量から発話とみなすか）を調整する。

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
