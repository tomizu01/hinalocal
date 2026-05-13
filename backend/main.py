from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import mss
import pygetwindow as gw
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

from google import genai
from google.genai import types as genai_types

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("hinaft")

CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cheer_override = os.environ.get("HINAFT_CHEER_FILE")
    if cheer_override:
        prompts_dir = Path(cfg["prompts"]["cheer_path"]).parent
        override_path = prompts_dir / cheer_override
        if not override_path.suffix:
            override_path = override_path.with_suffix(".md")
        if not override_path.exists():
            raise FileNotFoundError(
                f"指定されたcheerファイルが見つかりません: {override_path}"
            )
        cfg["prompts"]["cheer_path"] = str(override_path)
        logger.info("cheerプロンプトを上書き: %s", override_path)
    window_override = os.environ.get("HINAFT_WINDOW_TITLE")
    if window_override:
        cfg["capture"]["window_title"] = window_override
        logger.info("ウィンドウタイトルを上書き: %s", window_override)
    return cfg


def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                speaker TEXT NOT NULL,
                content TEXT NOT NULL,
                played INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur = conn.execute(
            "UPDATE messages SET played = 1 WHERE speaker = 'ai' AND played = 0"
        )
        if cur.rowcount > 0:
            logger.info("起動時クリーンアップ: 未再生AIメッセージ %d 件を既読化", cur.rowcount)
        conn.commit()


def db_connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_recent_history(db_path: str, limit: int) -> list[sqlite3.Row]:
    with db_connect(db_path) as conn:
        rows = conn.execute(
            "SELECT speaker, content FROM messages ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return list(reversed(rows))


def insert_message(db_path: str, speaker: str, content: str, played: int = 0) -> int:
    with db_connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO messages (speaker, content, played) VALUES (?, ?, ?)",
            (speaker, content, played),
        )
        conn.commit()
        return cur.lastrowid


def fetch_latest_player_id(db_path: str) -> int:
    with db_connect(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM messages WHERE speaker = 'player' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return row["id"] if row else 0


async def wait_for_new_player_message(
    db_path: str, total_seconds: float, check_interval: float
) -> bool:
    """5秒おきに新着プレイヤー発言を確認。新着があれば即True、タイムアウトでFalse。"""
    baseline = await asyncio.to_thread(fetch_latest_player_id, db_path)
    elapsed = 0.0
    while elapsed < total_seconds:
        await asyncio.sleep(check_interval)
        elapsed += check_interval
        latest = await asyncio.to_thread(fetch_latest_player_id, db_path)
        if latest > baseline:
            logger.info(
                "新着プレイヤー発言を検知 (id=%s)、待機を打ち切ってループ先頭へ", latest
            )
            return True
    return False


def fetch_next_unplayed_ai(db_path: str) -> sqlite3.Row | None:
    with db_connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, speaker, content, created_at FROM messages "
            "WHERE speaker = 'ai' AND played = 0 ORDER BY id ASC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE messages SET played = 1 WHERE id = ?", (row["id"],))
        conn.commit()
        return row


def history_to_text(rows: list[sqlite3.Row]) -> str:
    lines = []
    for r in rows:
        prefix = "AI" if r["speaker"] == "ai" else "プレイヤー"
        lines.append(f"{prefix}: {r['content']}")
    return "\n".join(lines)


def find_window_rect(title_substring: str) -> tuple[int, int, int, int] | None:
    """タイトル部分一致でウィンドウを探し、(left, top, width, height) を返す。見つからなければ None。"""
    try:
        wins = gw.getWindowsWithTitle(title_substring)
    except Exception:
        logger.exception("getWindowsWithTitle 失敗 (title=%s)", title_substring)
        return None
    for w in wins:
        try:
            if getattr(w, "isMinimized", False):
                continue
            if w.width <= 0 or w.height <= 0:
                continue
            return (w.left, w.top, w.width, w.height)
        except Exception:
            continue
    return None


def capture_window(title_substring: str) -> Image.Image | None:
    """対象ウィンドウ領域を mss で取得し PIL.Image を返す。未検出なら None。"""
    rect = find_window_rect(title_substring)
    if rect is None:
        return None
    left, top, width, height = rect
    with mss.mss() as sct:
        bbox = {"left": left, "top": top, "width": width, "height": height}
        shot = sct.grab(bbox)
    return Image.frombytes("RGB", shot.size, shot.rgb)


def process_and_save(
    image: Image.Image, dst: Path, clip: dict[str, int], resize_width: int
) -> None:
    img = image.convert("RGB")
    x1, y1, x2, y2 = clip["x1"], clip["y1"], clip["x2"], clip["y2"]
    if not (x1 == 0 and y1 == 0 and x2 == 0 and y2 == 0):
        x2 = min(x2, img.width)
        y2 = min(y2, img.height)
        img = img.crop((x1, y1, x2, y2))
    if resize_width and img.width != resize_width:
        ratio = resize_width / img.width
        new_size = (resize_width, int(img.height * ratio))
        img = img.resize(new_size, Image.LANCZOS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, format="JPEG", quality=85)


def read_prompt(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def build_gemini_client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


def call_gemini(
    client: genai.Client,
    model: str,
    character_prompt: str,
    cheer_prompt: str,
    history_text: str,
    image_path: Path,
) -> str:
    system_instruction = f"{character_prompt}\n\n---\n\n{cheer_prompt}"
    history_block = history_text if history_text else "（まだ会話履歴はありません）"
    user_text = (
        "これまでの会話履歴:\n"
        f"{history_block}\n\n"
        "添付の画像は現在のゲーム画面のキャプチャです。"
        "今までの会話履歴に自然につながる形で次に話す一言を出力してください。"
        "画像の内容も踏まえて、発話してください。"
        "出力は応援メッセージ本文のみで、説明や注釈は付けないでください。"
    )
    image_bytes = image_path.read_bytes()
    image_part = genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
    contents = [
        genai_types.Content(
            role="user",
            parts=[image_part, genai_types.Part.from_text(text=user_text)],
        )
    ]
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=genai_types.GenerateContentConfig(
            system_instruction=system_instruction,
        ),
    )
    text = (response.text or "").strip()
    return text


async def main_loop(app: FastAPI) -> None:
    cfg = app.state.config
    db_path = cfg["database"]["path"]
    window_title = cfg["capture"]["window_title"]
    processing_dir = Path(cfg["capture"]["processing_dir"])
    processing_dir.mkdir(parents=True, exist_ok=True)
    interval = cfg["loop"]["interval_seconds"]
    history_count = cfg["history"]["recent_count"]
    clip = cfg["image"]["clip"]
    resize_width = cfg["image"]["resize_width"]

    character_prompt = read_prompt(cfg["prompts"]["character_path"])
    cheer_prompt = read_prompt(cfg["prompts"]["cheer_path"])
    client: genai.Client = app.state.gemini_client

    logger.info(
        "メインループ開始: window_title=%r interval=%ss", window_title, interval
    )

    while True:
        try:
            try:
                captured = await asyncio.to_thread(capture_window, window_title)
            except Exception:
                logger.exception("ウィンドウキャプチャで例外")
                await asyncio.sleep(interval)
                continue
            if captured is None:
                logger.warning(
                    "対象ウィンドウが見つかりません (title=%r) — このループをスキップ",
                    window_title,
                )
                await asyncio.sleep(interval)
                continue

            unique_name = f"{int(time.time())}_{uuid.uuid4().hex[:8]}.jpg"
            processed = processing_dir / unique_name
            try:
                await asyncio.to_thread(
                    process_and_save, captured, processed, clip, resize_width
                )
            except Exception:
                logger.exception("画像加工失敗")
                await asyncio.sleep(interval)
                continue

            history_rows = await asyncio.to_thread(
                fetch_recent_history, db_path, history_count
            )
            history_text = history_to_text(history_rows)

            tier = app.state.model_tier
            model = app.state.model_names[tier]
            try:
                message = await asyncio.to_thread(
                    call_gemini,
                    client,
                    model,
                    character_prompt,
                    cheer_prompt,
                    history_text,
                    processed,
                )
            except Exception:
                logger.exception("Gemini呼び出し失敗 (tier=%s, model=%s)", tier, model)
                await asyncio.sleep(interval)
                continue

            if not message:
                logger.warning("Geminiが空応答を返した")
                await asyncio.sleep(interval)
                continue

            await asyncio.to_thread(insert_message, db_path, "ai", message, 0)
            logger.info("AI発話を保存 (tier=%s): %s", tier, message[:40])

        except asyncio.CancelledError:
            logger.info("メインループ停止")
            raise
        except Exception:
            logger.exception("メインループで予期せぬ例外")

        await wait_for_new_player_message(db_path, interval * 3, 5)


VALID_TIERS = ("flash", "pro")


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    app.state.config = cfg
    init_db(cfg["database"]["path"])
    app.state.gemini_client = build_gemini_client(cfg["gemini"]["api_key"])
    app.state.model_names = {
        "flash": cfg["gemini"]["flash_model"],
        "pro": cfg["gemini"]["pro_model"],
    }
    default_tier = cfg["gemini"].get("default_tier", "flash")
    if default_tier not in VALID_TIERS:
        logger.warning("default_tier=%s が不正なので flash に補正", default_tier)
        default_tier = "flash"
    app.state.model_tier = default_tier
    logger.info(
        "Geminiモデル初期化: tier=%s flash=%s pro=%s",
        default_tier,
        app.state.model_names["flash"],
        app.state.model_names["pro"],
    )
    Path(cfg["capture"]["processing_dir"]).mkdir(parents=True, exist_ok=True)

    task = asyncio.create_task(main_loop(app))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(lifespan=lifespan)


class PlayerMessage(BaseModel):
    content: str


class ModelTierUpdate(BaseModel):
    tier: str


@app.get("/api/messages/next")
async def get_next_message():
    cfg = app.state.config
    row = await asyncio.to_thread(fetch_next_unplayed_ai, cfg["database"]["path"])
    if row is None:
        return {}
    return {
        "id": row["id"],
        "speaker": row["speaker"],
        "content": row["content"],
        "created_at": row["created_at"],
    }


@app.get("/api/model")
async def get_model():
    return {
        "tier": app.state.model_tier,
        "models": app.state.model_names,
    }


@app.post("/api/model")
async def set_model(payload: ModelTierUpdate):
    tier = payload.tier
    if tier not in VALID_TIERS:
        raise HTTPException(status_code=400, detail=f"tier must be one of {VALID_TIERS}")
    app.state.model_tier = tier
    logger.info("モデル切替: tier=%s model=%s", tier, app.state.model_names[tier])
    return {"tier": tier, "model": app.state.model_names[tier]}


@app.post("/api/messages/player")
async def post_player_message(msg: PlayerMessage):
    text = msg.content.strip()
    if not text:
        raise HTTPException(status_code=400, detail="content is empty")
    cfg = app.state.config
    new_id = await asyncio.to_thread(
        insert_message, cfg["database"]["path"], "player", text, 1
    )
    return {"id": new_id}


FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="hinaft backend")
    parser.add_argument(
        "--cheer",
        help="cheer.mdの代わりに使うファイル名 (backend/prompts/ 配下)。拡張子省略可",
    )
    parser.add_argument(
        "--window",
        help="config.yaml の capture.window_title を上書きするウィンドウタイトル（部分一致）",
    )
    args = parser.parse_args()
    if args.cheer:
        os.environ["HINAFT_CHEER_FILE"] = args.cheer
    if args.window:
        os.environ["HINAFT_WINDOW_TITLE"] = args.window

    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
