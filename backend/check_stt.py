"""ローカルSTT（faster-whisper）の動作を1段階ずつ確認する診断スクリプト。

`python backend\\main.py` が無言で終了する等、原因が分からないときに使う。
どの段階まで進んだかを都度表示するので、途中でプロセスごと落ちた場合でも
「どこで落ちたか」が分かる。

モデルのロードは **子プロセス** で試すため、ネイティブ側（CUDA / cuDNN）で
プロセスごとクラッシュしても診断は続行し、終了コードから原因を推定する。

使い方（venv を有効化した状態で）:
    python backend\\check_stt.py

出力はそのまま貼り付けて共有できる内容にしてある。
"""

from __future__ import annotations

import argparse
import ctypes
import glob
import io
import os
import site
import subprocess
import sys
import time
import wave
from pathlib import Path

BACKEND_DIR = Path(__file__).parent
sys.path.insert(0, str(BACKEND_DIR))

# 確認する DLL。venv 外の同名DLLが先に読まれるとバージョン不一致でプロセスごと落ちる。
# cudnn64_9.dll は ctranslate2 が、cudnn_*64_9.dll は nvidia-cudnn-cu12 が入れる。
# libiomp5md.dll（Intel OpenMP）は他の OpenMP と二重ロードされると abort する定番の地雷。
REQUIRED_DLLS = (
    "ctranslate2.dll",
    "libiomp5md.dll",
    "cublas64_12.dll",
    "cublasLt64_12.dll",
    "cudnn64_9.dll",
    "cudnn_graph64_9.dll",
    "cudnn_ops64_9.dll",
)

# よくあるネイティブクラッシュの終了コード
CRASH_CODES = {
    0xC0000005: "アクセス違反（DLLの不整合やドライバ非対応の可能性）",
    0xC0000409: "スタック破壊 / セキュリティチェック失敗",
    0xC000001D: "不正な命令（CPU/GPU がその命令セットに非対応）",
    0xC0000135: "DLL が見つからない",
    0xC0000142: "DLL の初期化に失敗",
    0xC0000374: "ヒープ破壊",
}


def step(msg: str) -> None:
    # フラッシュしないと、この直後にネイティブ側でクラッシュした場合に出力が消える
    print(f"[check] {msg}", flush=True)


def physical_memory() -> str:
    """搭載/空きメモリを返す（CPU実行時は数GBの空きが要る）。"""
    try:

        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MemoryStatusEx()
        stat.dwLength = ctypes.sizeof(MemoryStatusEx)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return f"{stat.ullTotalPhys / 1e9:.1f} GB 中 {stat.ullAvailPhys / 1e9:.1f} GB 空き"
    except Exception as e:
        return f"取得できません: {e}"


def loaded_dll_path(name: str) -> str | None:
    """DLL を名前で解決してロードし、実際に読み込まれたファイルのパスを返す。

    別の CUDA（システムに入れた CUDA Toolkit 等）の DLL が先に見つかると、
    バージョン不一致でネイティブクラッシュする。どれが読まれたかを確認するために使う。
    """
    try:
        handle = ctypes.WinDLL(name)
    except OSError:
        return None
    buf = ctypes.create_unicode_buffer(2048)
    ctypes.windll.kernel32.GetModuleFileNameW(
        ctypes.c_void_p(handle._handle), buf, len(buf)
    )
    return buf.value or None


def describe_exit_code(rc: int) -> str:
    if rc == 0:
        return "成功"
    code = rc & 0xFFFFFFFF
    if code in CRASH_CODES:
        return f"クラッシュ 0x{code:08X} … {CRASH_CODES[code]}"
    if rc == 1:
        return "Python の例外で終了"
    return f"異常終了 rc={rc} (0x{code:08X})"


def load_stt_config() -> dict:
    import yaml

    cfg_path = BACKEND_DIR / "config.yaml"
    if not cfg_path.exists():
        return {}
    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return cfg.get("stt") or {}


# --------------------------------------------------------------------------
# 子プロセス側：指定の device / compute_type でロードして推論まで試す
# --------------------------------------------------------------------------


def try_load(device: str, compute_type: str) -> int:
    from stt import SttEngine

    cfg = dict(load_stt_config())
    cfg["device"] = device
    cfg["compute_type"] = compute_type
    # 失敗を CPU で隠さないため、この診断ではフォールバックを切る
    cfg["fallback_to_cpu"] = False

    engine = SttEngine(cfg)
    started = time.perf_counter()
    engine.load()
    load_sec = time.perf_counter() - started

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)
    started = time.perf_counter()
    engine._transcribe_sync(buf.getvalue())
    print(f"load={load_sec:.1f}秒 infer={time.perf_counter() - started:.1f}秒", flush=True)
    return 0


def run_load_attempt(device: str, compute_type: str) -> tuple[int, str]:
    """子プロセスでロードを試し、(終了コード, 出力の末尾) を返す。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"  # 子プロセスのログ（日本語）を確実に読めるようにする
    proc = subprocess.run(
        [sys.executable, str(Path(__file__)), "--try-load", device, compute_type],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        env=env,
    )
    tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    return proc.returncode, (tail[-1] if tail else "")


# --------------------------------------------------------------------------
# 親プロセス側：段階ごとの診断
# --------------------------------------------------------------------------


def main() -> int:
    step(f"Python      : {sys.version}")
    step(f"実行ファイル: {sys.executable}")
    step(f"作業ディレクトリ: {os.getcwd()}")
    step(f"メモリ      : {physical_memory()}")

    # --- 1. NVIDIA ドライバ ---------------------------------------------
    step("1) NVIDIA ドライバを確認します (nvidia-smi)")
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total,memory.used",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if out.returncode == 0:
            step(f"   GPU: {out.stdout.strip()}")
            step("   ※ ドライバは R525 以降が必要（CUDA 12.x のライブラリを使うため）")
        else:
            step(f"   nvidia-smi が失敗しました (rc={out.returncode}): {out.stderr.strip()}")
    except FileNotFoundError:
        step("   nvidia-smi が見つかりません（ドライバ未導入の可能性）")
    except Exception as e:
        step(f"   nvidia-smi の実行に失敗: {e}")

    # --- 2. パッケージ ---------------------------------------------------
    step("2) 必要パッケージを確認します")
    try:
        import ctranslate2
        import faster_whisper

        step(f"   ctranslate2   : {ctranslate2.__version__}")
        step(f"   faster-whisper: {faster_whisper.__version__}")
    except Exception as e:
        step(f"   NG: import に失敗しました: {e!r}")
        step("   → venv を有効化しているか、setup.ps1 が成功しているか確認してください")
        return 1

    # --- 3. CUDA DLL -----------------------------------------------------
    step("3) CUDA ライブラリ (cuBLAS / cuDNN) を確認します")
    found_dirs = []
    for root in list(site.getsitepackages()) + [site.getusersitepackages()]:
        if isinstance(root, str):
            found_dirs.extend(glob.glob(os.path.join(root, "nvidia", "*", "bin")))
    if found_dirs:
        for d in found_dirs:
            step(f"   {d}")
    else:
        step("   NG: nvidia-cublas-cu12 / nvidia-cudnn-cu12 が見つかりません")
        step("   → pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 を実行してください")

    from stt import add_cuda_dll_directories

    add_cuda_dll_directories()

    # DLL が「存在するか」ではなく「実際にどれが読み込まれるか」まで見る。
    # 別途入れた CUDA Toolkit / oneAPI / Anaconda 等の同名DLLが優先されると、
    # バージョン不一致（特に cuDNN と OpenMP）でプロセスごと落ちる。
    venv_roots = [
        os.path.normcase(str(Path(sys.executable).parent.parent)),
    ]
    outsiders = []
    for name in REQUIRED_DLLS:
        actual = loaded_dll_path(name)
        if actual is None:
            step(f"   NG: {name} をロードできません（見つからない、または依存DLL不足）")
            continue
        inside = any(os.path.normcase(actual).startswith(r) for r in venv_roots)
        if inside:
            step(f"   {name}: OK ({actual})")
        else:
            step(f"   ※ 要注意: {name} が venv の外から読まれています → {actual}")
            outsiders.append((name, actual))
    if outsiders:
        step("   ↑ これがクラッシュの原因になり得ます。PATH に別の CUDA / OpenMP が")
        step("     入っていないか確認してください（下の 3b で PATH を表示します）")
        step("3b) PATH の中で上記DLLを含むディレクトリ")
        for entry in os.environ.get("PATH", "").split(os.pathsep):
            entry = entry.strip()
            if not entry:
                continue
            for name, _ in outsiders:
                if os.path.exists(os.path.join(entry, name)):
                    step(f"   {entry}  ({name})")

    # --- 4. CUDA デバイス -------------------------------------------------
    step("4) CUDA デバイスを確認します")
    try:
        count = ctranslate2.get_cuda_device_count()
        step(f"   CUDA デバイス数: {count}")
        if count == 0:
            step("   NG: GPU が見えていません（ドライバは R525 以降が必要）")
        else:
            step(
                "   対応 compute_type: "
                f"{sorted(ctranslate2.get_supported_compute_types('cuda'))}"
            )
    except Exception as e:
        step(f"   NG: CUDA の初期化に失敗: {e!r}")

    # --- 5. 設定とモデル --------------------------------------------------
    step("5) config.yaml の stt 設定とモデルの実体を確認します")
    stt_cfg = load_stt_config()
    if not stt_cfg:
        step("   NG: config.yaml が無いか、stt: セクションがありません")
        return 1
    for key in ("enabled", "model", "model_dir", "device", "compute_type", "language"):
        step(f"   stt.{key} = {stt_cfg.get(key)!r}")

    model_dir = stt_cfg.get("model_dir")
    if model_dir:
        path = Path(model_dir)
        if not path.is_absolute():
            path = BACKEND_DIR.parent / path
        model_bin = path / "model.bin"
        if model_bin.exists():
            step(f"   model.bin: {model_bin.stat().st_size:,} バイト {model_bin}")
            # コピー時の破損を疑えるよう、ハッシュを出して他機と比較できるようにする
            import hashlib

            h = hashlib.sha256()
            with model_bin.open("rb") as f:
                for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
                    h.update(chunk)
            step(f"   model.bin SHA256: {h.hexdigest()}")
            for extra in ("config.json", "tokenizer.json", "vocabulary.json"):
                p = path / extra
                state = f"{p.stat().st_size:,} バイト" if p.exists() else "**ありません**"
                step(f"   {extra}: {state}")
        else:
            step(f"   NG: model.bin がありません: {model_bin}")
            step("   → python backend\\download_stt_model.py を実行してください")
            return 1

    # --- 6. モデルのロードと推論 -------------------------------------------
    # 子プロセスで試すので、ネイティブ側でクラッシュしても診断は続行できる
    configured = (
        str(stt_cfg.get("device") or "cuda"),
        str(stt_cfg.get("compute_type") or "int8_float16"),
    )
    step(f"6) 設定どおり device={configured[0]} compute_type={configured[1]} でロードを試します")
    rc, tail = run_load_attempt(*configured)
    step(f"   → {describe_exit_code(rc)}{(' / ' + tail) if tail else ''}")
    if rc == 0:
        step("すべて成功しました。STT は動作可能な状態です。")
        return 0

    # 失敗したので、どこまでなら動くのかを切り分ける
    step("7) 失敗したため、他の組み合わせで切り分けます（各1回ずつロードします）")
    fallbacks = [
        ("cuda", "float16"),
        ("cuda", "float32"),
        ("cuda", "int8"),
        ("cpu", "int8"),
    ]
    results = []
    for device, compute_type in fallbacks:
        if (device, compute_type) == configured:
            continue
        step(f"   試行: device={device} compute_type={compute_type} ...")
        rc2, tail2 = run_load_attempt(device, compute_type)
        step(f"      → {describe_exit_code(rc2)}{(' / ' + tail2) if tail2 else ''}")
        results.append((device, compute_type, rc2))

    ok_cuda = [r for r in results if r[0] == "cuda" and r[2] == 0]
    ok_cpu = [r for r in results if r[0] == "cpu" and r[2] == 0]
    step("8) 判定")
    if ok_cuda:
        d, c, _ = ok_cuda[0]
        step(f"   GPU では compute_type={c} なら動きます。")
        step(f"   → config.yaml の stt.compute_type を \"{c}\" に変更してください。")
    elif ok_cpu:
        step("   GPU では全滅、CPU では動作します。ドライバ／CUDAライブラリ側の問題です。")
        step("   → nvidia-smi のドライバ版数（R525 以降か）を確認してください。")
        step('   → 暫定運用するなら config.yaml で stt.device: "cpu" にしてください。')
    else:
        step("   CPU でも失敗しました。モデルファイルの破損か、venv の不整合が疑われます。")
        step("   → backend\\venv を削除して setup.ps1 を実行し直してください。")
        step("   → それでも直らない場合はモデルを再取得してください:")
        step("      python backend\\download_stt_model.py")
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="STT の動作診断")
    parser.add_argument(
        "--try-load",
        nargs=2,
        metavar=("DEVICE", "COMPUTE_TYPE"),
        help="内部用: 指定の設定でモデルをロードして推論まで試す（子プロセスとして実行される）",
    )
    args = parser.parse_args()
    if args.try_load:
        sys.exit(try_load(args.try_load[0], args.try_load[1]))
    sys.exit(main())
