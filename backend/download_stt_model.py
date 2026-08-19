"""faster-whisper のモデルをローカル（backend/models/）へ事前ダウンロードするスクリプト。

STT は実行時に外部ネットワークへ接続しない方針のため、モデルは
**セットアップ時にこのスクリプトで取得しておく**（ここだけはインターネットが必要）。
取得後は backend/config.yaml の stt.model_dir を参照してオフラインで動作する。

使い方（venv を有効化した状態で）:
    python backend\\download_stt_model.py                    # config.yaml の stt.model を取得
    python backend\\download_stt_model.py --model medium     # モデル名を指定して取得
    python backend\\download_stt_model.py --list             # 指定できるモデル名の一覧
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

BACKEND_DIR = Path(__file__).parent
CONFIG_PATH = BACKEND_DIR / "config.yaml"
DEFAULT_MODELS_DIR = BACKEND_DIR / "models"


def load_stt_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return cfg.get("stt") or {}


def main() -> int:
    from faster_whisper.utils import _MODELS

    parser = argparse.ArgumentParser(
        description="faster-whisper モデルをローカルに事前ダウンロードする"
    )
    parser.add_argument(
        "--model",
        help="モデル名（例: large-v3-turbo / medium / small）または"
        " Hugging Face のリポジトリ名。既定は config.yaml の stt.model",
    )
    parser.add_argument(
        "--dest",
        help=f"保存先ディレクトリ。既定は {DEFAULT_MODELS_DIR}\\<モデル名>",
    )
    parser.add_argument(
        "--list", action="store_true", help="指定できるモデル名の一覧を表示して終了"
    )
    args = parser.parse_args()

    if args.list:
        print("指定できるモデル名（右は Hugging Face リポジトリ）:")
        for name, repo in _MODELS.items():
            print(f"  {name:<20} {repo}")
        return 0

    stt_cfg = load_stt_config()
    model = args.model or stt_cfg.get("model") or "large-v3-turbo"
    repo_id = _MODELS.get(model, model)

    if args.dest:
        dest = Path(args.dest)
    else:
        # config.yaml に model_dir があればそれを、無ければ models/<モデル名>
        configured = stt_cfg.get("model_dir")
        if configured and (not args.model or args.model == stt_cfg.get("model")):
            dest = Path(configured)
        else:
            dest = DEFAULT_MODELS_DIR / model.replace("/", "_")
    dest = dest if dest.is_absolute() else (BACKEND_DIR.parent / dest)

    print(f"モデル       : {model}")
    print(f"リポジトリ   : {repo_id}")
    print(f"保存先       : {dest}")
    print("ダウンロード中...（初回は数分かかります）")

    from huggingface_hub import snapshot_download

    dest.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=repo_id,
        local_dir=str(dest),
        allow_patterns=[
            "config.json",
            "preprocessor_config.json",
            "model.bin",
            "tokenizer.json",
            "vocabulary.*",
        ],
    )
    print("完了しました。")
    print(
        "backend/config.yaml の stt.model_dir に次を設定してください:\n"
        f"  model_dir: \"{dest.as_posix()}\""
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
