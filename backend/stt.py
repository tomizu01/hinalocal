"""ローカル音声認識（STT）: faster-whisper をこのマシン上の GPU で動かす。

外部ネットワークへ接続できなくても動作させるため、
- モデルは `backend/download_stt_model.py` でセットアップ時にローカルへ取得しておく
- 実行時は `local_files_only=True` でローカルディレクトリだけを読む（HTTP アクセスなし）
方針とする。

ブラウザの Web Speech API は音声を Google のサーバへ送るためオフラインでは使えない。
代わりにフロントエンドが録音した音声を `POST /api/stt` へ送り、ここで文字起こしする。
"""

from __future__ import annotations

import asyncio
import ctypes
import glob
import io
import logging
import os
import site
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("hinalocal.stt")

BACKEND_DIR = Path(__file__).parent

# 無音や環境音に対して Whisper が吐きやすい定型の幻聴（日本語）。
# 完全一致（句読点・空白を除去して比較）した場合のみ捨てる。
DEFAULT_HALLUCINATIONS: tuple[str, ...] = (
    "ご視聴ありがとうございました",
    "ご視聴ありがとうございましたー",
    "ご清聴ありがとうございました",
    "おやすみなさい",
    "チャンネル登録お願いします",
    "最後までご視聴いただきありがとうございます",
    "ありがとうございました",
    "字幕視聴ありがとうございました",
    "エンディング",
    "おわり",
    "終わり",
)

_STRIP_CHARS = " \t\r\n。、．，・…!?！？「」『』()（）~〜ー-"

# faster-whisper が音声を読めるサンプリングレート（Whisper の入力仕様）
SAMPLE_RATE = 16000


class AudioDecodeError(Exception):
    """送られてきたデータを音声としてデコードできなかった（＝クライアント側の問題）。"""


def _normalize_for_compare(text: str) -> str:
    return "".join(ch for ch in text if ch not in _STRIP_CHARS)


def add_cuda_dll_directories() -> list[str]:
    """pip で入れた CUDA ライブラリ（nvidia-cublas-cu12 / nvidia-cudnn-cu12）の
    DLL を Windows の検索パスへ追加し、追加したディレクトリを返す。

    ctranslate2 は PyTorch と違い site-packages/nvidia/*/bin を自動では見に行かない。
    追加しないと GPU 実行時に「cublas64_12.dll が見つからない」等で落ちる。
    """
    if sys.platform != "win32":
        return []
    roots: list[str] = []
    try:
        roots.extend(site.getsitepackages())
    except Exception:  # pragma: no cover - 環境依存
        pass
    user_site = site.getusersitepackages()
    if isinstance(user_site, str):
        roots.append(user_site)
    added: list[str] = []
    for root in roots:
        for dll_dir in glob.glob(os.path.join(root, "nvidia", "*", "bin")):
            try:
                os.add_dll_directory(dll_dir)
                added.append(dll_dir)
            except OSError:
                continue
    if added:
        logger.debug("CUDA DLL 検索パスを %d 件追加しました", len(added))
    return added


def preload_cuda_libraries() -> None:
    """GPU 実行に必要な CUDA ライブラリを、フルパス指定で先に読み込んでおく。

    ctranslate2 は **最初の推論の直前** に cuBLAS を遅延ロードするため、
    見つからない場合のエラーが「起動時」ではなく「最初に喋ったとき」に出る。
    しかもメッセージが `Library cublas64_12.dll is not found or cannot be loaded` だけで
    原因（＝pip パッケージ未導入）に辿り着きにくい。
    ここで先に読み込んでおけば、問題があれば起動時に気付ける。
    """
    if sys.platform != "win32":
        return
    dll_dirs = add_cuda_dll_directories()

    def find(name: str) -> str | None:
        for d in dll_dirs:
            path = os.path.join(d, name)
            if os.path.exists(path):
                return path
        return None

    # cuBLAS は必須。無ければ pip パッケージが入っていない
    for name in ("cublasLt64_12.dll", "cublas64_12.dll"):
        path = find(name)
        if path is None:
            logger.warning(
                "%s が見つかりません。GPU での音声認識はできません。\n"
                "  venv を有効化して次を実行してください:\n"
                "    pip install -r backend\\requirements.txt\n"
                "  （nvidia-cublas-cu12 / nvidia-cudnn-cu12 が導入されます）",
                name,
            )
            continue
        try:
            ctypes.WinDLL(path)
            logger.debug("CUDA ライブラリを事前ロード: %s", path)
        except OSError as e:
            logger.warning("%s をロードできません: %s", path, e)

    # cuDNN の実体（ディスパッチャ cudnn64_9.dll は ctranslate2 が同梱している）。
    # 無くても動く構成があり得るので、失敗しても警告に留める。
    for name in ("cudnn_graph64_9.dll", "cudnn_ops64_9.dll"):
        path = find(name)
        if path is None:
            continue
        try:
            ctypes.WinDLL(path)
            logger.debug("CUDA ライブラリを事前ロード: %s", path)
        except OSError as e:
            logger.warning("%s をロードできません: %s", path, e)


class SttEngine:
    """faster-whisper のモデルを1つ抱えて文字起こしを行う。

    - モデルのロードは起動時に1回だけ（VRAM に載せっぱなしにして毎回の遅延を避ける）
    - 文字起こしは CPU/GPU を占有するので asyncio.Lock で1件ずつ直列に処理する
    - 実処理は同期関数なので asyncio.to_thread でイベントループを塞がないようにする
    """

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.enabled: bool = bool(cfg.get("enabled", True))
        self.model_name: str = str(cfg.get("model") or "large-v3-turbo")
        self.model_dir: str | None = cfg.get("model_dir") or None
        self.device: str = str(cfg.get("device") or "cuda")
        self.device_index: int = int(cfg.get("device_index", 0))
        self.compute_type: str = str(cfg.get("compute_type") or "int8_float16")
        self.cpu_threads: int = int(cfg.get("cpu_threads", 0))
        self.fallback_to_cpu: bool = bool(cfg.get("fallback_to_cpu", True))

        self.language: str | None = cfg.get("language") or None
        self.beam_size: int = int(cfg.get("beam_size", 5))
        self.vad_filter: bool = bool(cfg.get("vad_filter", True))
        self.initial_prompt: str | None = cfg.get("initial_prompt") or None
        self.no_speech_threshold: float = float(cfg.get("no_speech_threshold", 0.6))
        self.log_prob_threshold: float = float(cfg.get("log_prob_threshold", -1.0))

        extra = cfg.get("hallucination_blocklist") or []
        if not isinstance(extra, list):
            raise ValueError("stt.hallucination_blocklist はリストで指定してください")
        self._blocklist = {
            _normalize_for_compare(str(s))
            for s in (*DEFAULT_HALLUCINATIONS, *extra)
            if str(s).strip()
        }

        self._model = None
        self._lock = asyncio.Lock()
        self.ready = False

    # ------------------------------------------------------------------ load

    def _resolve_model_source(self) -> tuple[str, bool]:
        """(WhisperModel に渡す文字列, local_files_only) を返す。

        model_dir が指定されていればそのディレクトリを使い、ネットワークを一切見ない。
        未指定なら Hugging Face のキャッシュ解決に任せる（初回のみ要ネットワーク）。
        """
        if self.model_dir:
            path = Path(self.model_dir)
            if not path.is_absolute():
                path = BACKEND_DIR.parent / path
            if not (path / "model.bin").exists():
                raise FileNotFoundError(
                    f"STT モデルが見つかりません: {path}\n"
                    "先に `python backend\\download_stt_model.py` を実行して"
                    "モデルをローカルへ取得してください。"
                )
            return str(path), True
        return self.model_name, False

    def load(self) -> None:
        """モデルを VRAM に載せる（同期・重い処理）。"""
        if self._model is not None:
            return
        if self.device == "cuda":
            preload_cuda_libraries()
        else:
            add_cuda_dll_directories()
        from faster_whisper import WhisperModel

        source, local_only = self._resolve_model_source()
        device = self.device
        compute_type = self.compute_type
        started = time.perf_counter()
        try:
            self._model = WhisperModel(
                source,
                device=device,
                device_index=self.device_index,
                compute_type=compute_type,
                cpu_threads=self.cpu_threads,
                local_files_only=local_only,
            )
        except Exception:
            if device != "cuda" or not self.fallback_to_cpu:
                raise
            logger.exception(
                "STT モデルの GPU ロードに失敗しました。CPU にフォールバックします"
                "（認識に時間がかかります）"
            )
            device = "cpu"
            compute_type = "int8"
            self._model = WhisperModel(
                source,
                device=device,
                compute_type=compute_type,
                cpu_threads=self.cpu_threads,
                local_files_only=local_only,
            )
        self.device = device
        self.compute_type = compute_type
        logger.info(
            "STT モデルをロードしました: source=%s device=%s compute_type=%s (%.1f秒)",
            source,
            device,
            compute_type,
            time.perf_counter() - started,
        )

    def warmup(self) -> None:
        """短い無音を1回流して CUDA カーネルの初期化を済ませる（同期・重い処理）。

        初回推論は GPU カーネルの JIT コンパイルで十数秒かかることがある。
        起動時にここで消化しておかないと、最初の発話だけ極端に待たされる。
        """
        if self._model is None:
            return
        import wave

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 16000)  # 1秒の無音
        started = time.perf_counter()
        try:
            segments, _ = self._model.transcribe(
                io.BytesIO(buf.getvalue()),
                language=self.language,
                beam_size=self.beam_size,
                vad_filter=False,
            )
            list(segments)  # ジェネレータなので消費して初めて推論が走る
        except Exception:
            logger.exception("STT のウォームアップに失敗（動作は継続します）")
            return
        logger.info("STT ウォームアップ完了 (%.1f秒)", time.perf_counter() - started)

    # -------------------------------------------------------------- inference

    def _transcribe_sync(self, audio: bytes) -> str:
        if self._model is None:
            raise RuntimeError("STT モデルが未ロードです")
        started = time.perf_counter()
        # デコードを先に済ませて、不正データ（クライアント起因）と
        # 推論の失敗（サーバ起因）を呼び出し側で区別できるようにする。
        from faster_whisper.audio import decode_audio

        try:
            waveform = decode_audio(io.BytesIO(audio), sampling_rate=SAMPLE_RATE)
        except Exception as e:
            raise AudioDecodeError(str(e)) from e

        segments, info = self._model.transcribe(
            waveform,
            language=self.language,
            beam_size=self.beam_size,
            vad_filter=self.vad_filter,
            initial_prompt=self.initial_prompt,
            no_speech_threshold=self.no_speech_threshold,
            log_prob_threshold=self.log_prob_threshold,
            # 直前の認識結果に引きずられてループするのを防ぐ（1発話ごとに独立）
            condition_on_previous_text=False,
        )

        parts: list[str] = []
        for seg in segments:
            text = (seg.text or "").strip()
            if not text:
                continue
            if seg.no_speech_prob is not None and seg.no_speech_prob > self.no_speech_threshold:
                logger.info(
                    "STT: 無音判定でセグメントを破棄 (no_speech_prob=%.2f): %r",
                    seg.no_speech_prob,
                    text,
                )
                continue
            if _normalize_for_compare(text) in self._blocklist:
                logger.info("STT: 幻聴パターンに一致したので破棄: %r", text)
                continue
            parts.append(text)

        result = "".join(parts).strip()
        logger.info(
            "STT: %.2f秒の音声を %.2f秒で認識 (lang=%s prob=%.2f): %r",
            getattr(info, "duration", 0.0) or 0.0,
            time.perf_counter() - started,
            getattr(info, "language", "?"),
            getattr(info, "language_probability", 0.0) or 0.0,
            result,
        )
        return result

    async def transcribe(self, audio: bytes) -> str:
        """音声バイト列（webm/opus, wav, mp3 など）を文字起こしして返す。

        デコードは faster-whisper が同梱の PyAV で行うため ffmpeg の別途インストールは不要。
        """
        async with self._lock:
            return await asyncio.to_thread(self._transcribe_sync, audio)

    async def prepare(self) -> None:
        """モデルのロードとウォームアップ（起動直後にバックグラウンドで実行する想定）。

        ロード中に来た `/api/stt` はロックで待たされるだけで、失敗にはならない。
        """
        async with self._lock:
            try:
                await asyncio.to_thread(self.load)
            except Exception:
                # ここで落としてもアプリ本体（実況ループ）は動かせるので、
                # ログだけ残して STT 無効状態のまま継続する。
                logger.exception(
                    "STT モデルのロードに失敗しました。音声入力は使えません"
                    "（テキスト入力は利用できます）"
                )
                return
            await asyncio.to_thread(self.warmup)
            self.ready = True


def build_stt_engine(cfg: dict[str, Any] | None) -> SttEngine | None:
    """config.yaml の stt セクションから SttEngine を作る。無効なら None。"""
    stt_cfg = cfg or {}
    if not stt_cfg.get("enabled", True):
        logger.info("STT は無効化されています (stt.enabled: false)")
        return None
    return SttEngine(stt_cfg)
