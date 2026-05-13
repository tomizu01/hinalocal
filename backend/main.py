from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
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
logger = logging.getLogger("hinalive")

CONFIG_PATH = Path(__file__).parent / "config.yaml"

CHARACTER_ID_RE = re.compile(r"^[A-Za-z]{1,16}$")


def validate_character_id(cid: Any) -> str:
    """character_id を A-Za-z 1-16文字に制限。違反したら ValueError。"""
    if not isinstance(cid, str) or not CHARACTER_ID_RE.match(cid):
        raise ValueError(
            f"character_id は A-Za-z 1-16文字である必要があります: {cid!r}"
        )
    return cid


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cheer_override = os.environ.get("HINALIVE_CHEER_FILE")
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
    window_override = os.environ.get("HINALIVE_WINDOW_TITLE")
    if window_override:
        cfg["capture"]["window_title"] = window_override
        logger.info("ウィンドウタイトルを上書き: %s", window_override)
    # character_id 検証
    validate_character_id(cfg["character"]["current_id"])
    return cfg


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def init_db(db_path: str, default_character_id: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                speaker TEXT NOT NULL,
                content TEXT NOT NULL,
                played INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                character_id TEXT,
                day INTEGER
            )
            """
        )
        # messages に character_id カラムを追加（未追加時のみ）
        if not _column_exists(conn, "messages", "character_id"):
            conn.execute("ALTER TABLE messages ADD COLUMN character_id TEXT")
            logger.info("messages.character_id カラムを追加しました")
        # 未設定行を current_id で backfill
        cur = conn.execute(
            "UPDATE messages SET character_id = ? WHERE character_id IS NULL",
            (default_character_id,),
        )
        if cur.rowcount > 0:
            logger.info(
                "messages.character_id を %d 行に backfill (= %s)",
                cur.rowcount,
                default_character_id,
            )
        # messages.day カラム
        if not _column_exists(conn, "messages", "day"):
            conn.execute("ALTER TABLE messages ADD COLUMN day INTEGER")
            logger.info("messages.day カラムを追加しました")
        cur = conn.execute("UPDATE messages SET day = 1 WHERE day IS NULL")
        if cur.rowcount > 0:
            logger.info("messages.day を %d 行 backfill (=1)", cur.rowcount)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_char_id "
            "ON messages(character_id, id DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_char_day "
            "ON messages(character_id, day, id DESC)"
        )

        # 中期記憶テーブル
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mid_term_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id TEXT NOT NULL,
                summary TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                day INTEGER,
                last_message_id INTEGER
            )
            """
        )
        if not _column_exists(conn, "mid_term_memories", "day"):
            conn.execute("ALTER TABLE mid_term_memories ADD COLUMN day INTEGER")
            logger.info("mid_term_memories.day カラムを追加しました")
        cur = conn.execute(
            "UPDATE mid_term_memories SET day = 1 WHERE day IS NULL"
        )
        if cur.rowcount > 0:
            logger.info("mid_term_memories.day を %d 行 backfill (=1)", cur.rowcount)
        if not _column_exists(conn, "mid_term_memories", "last_message_id"):
            conn.execute(
                "ALTER TABLE mid_term_memories ADD COLUMN last_message_id INTEGER"
            )
            logger.info("mid_term_memories.last_message_id カラムを追加しました")
        cur = conn.execute(
            "UPDATE mid_term_memories SET last_message_id = 0 "
            "WHERE last_message_id IS NULL"
        )
        if cur.rowcount > 0:
            logger.info(
                "mid_term_memories.last_message_id を %d 行 backfill (=0)",
                cur.rowcount,
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mid_term_char "
            "ON mid_term_memories(character_id, id DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mid_term_char_day "
            "ON mid_term_memories(character_id, day, id DESC)"
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


def fetch_recent_history(
    db_path: str, character_id: str, limit: int, day: int
) -> list[sqlite3.Row]:
    """現在のキャラ・day の直近 limit 件を古い順で返す。"""
    with db_connect(db_path) as conn:
        rows = conn.execute(
            "SELECT speaker, content FROM messages "
            "WHERE character_id = ? AND day = ? "
            "ORDER BY id DESC LIMIT ?",
            (character_id, day, limit),
        ).fetchall()
    return list(reversed(rows))


def insert_message(
    db_path: str,
    character_id: str,
    speaker: str,
    content: str,
    day: int,
    played: int = 0,
) -> int:
    with db_connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO messages (character_id, speaker, content, played, day) "
            "VALUES (?, ?, ?, ?, ?)",
            (character_id, speaker, content, played, day),
        )
        conn.commit()
        return cur.lastrowid


def fetch_latest_player_id(db_path: str, character_id: str) -> int:
    with db_connect(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM messages "
            "WHERE character_id = ? AND speaker = 'player' "
            "ORDER BY id DESC LIMIT 1",
            (character_id,),
        ).fetchone()
    return row["id"] if row else 0


def fetch_latest_day(db_path: str, character_id: str) -> int | None:
    """現在のキャラの中で最新 created_at の行の day を返す。なければ None。"""
    with db_connect(db_path) as conn:
        row = conn.execute(
            "SELECT day FROM messages WHERE character_id = ? "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (character_id,),
        ).fetchone()
    if row is None or row["day"] is None:
        return None
    return int(row["day"])


def fetch_mid_term_last_message_id(
    db_path: str, character_id: str, day: int
) -> int:
    """現在のキャラ・day の mid_term_memories.last_message_id の最大値。なければ 0。"""
    with db_connect(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(last_message_id) AS m FROM mid_term_memories "
            "WHERE character_id = ? AND day = ?",
            (character_id, day),
        ).fetchone()
    return row["m"] if row and row["m"] is not None else 0


def fetch_max_message_id_today(
    db_path: str, character_id: str, day: int
) -> int:
    """現在のキャラ・day の messages.id の最大値。なければ 0。"""
    with db_connect(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(id) AS m FROM messages "
            "WHERE character_id = ? AND day = ?",
            (character_id, day),
        ).fetchone()
    return row["m"] if row and row["m"] is not None else 0


def count_messages_after(
    db_path: str, character_id: str, day: int, last_message_id: int
) -> int:
    """現在のキャラ・day で id > last_message_id の messages 件数。"""
    with db_connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM messages "
            "WHERE character_id = ? AND day = ? AND id > ?",
            (character_id, day, last_message_id),
        ).fetchone()
    return row["c"] if row else 0


def insert_mid_term_memory(
    db_path: str,
    character_id: str,
    summary: str,
    day: int,
    last_message_id: int,
) -> int:
    with db_connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO mid_term_memories "
            "(character_id, summary, day, last_message_id) VALUES (?, ?, ?, ?)",
            (character_id, summary, day, last_message_id),
        )
        conn.commit()
        return cur.lastrowid


def fetch_recent_mid_term_memories(
    db_path: str, character_id: str, limit: int
) -> list[sqlite3.Row]:
    """直近の中期記憶を古い順で返す。"""
    with db_connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, summary, created_at FROM mid_term_memories "
            "WHERE character_id = ? ORDER BY id DESC LIMIT ?",
            (character_id, limit),
        ).fetchall()
    return list(reversed(rows))


async def wait_for_new_player_message(
    db_path: str, character_id: str, total_seconds: float, check_interval: float
) -> bool:
    """5秒おきに新着プレイヤー発言を確認。新着があれば即True、タイムアウトでFalse。"""
    baseline = await asyncio.to_thread(fetch_latest_player_id, db_path, character_id)
    elapsed = 0.0
    while elapsed < total_seconds:
        await asyncio.sleep(check_interval)
        elapsed += check_interval
        latest = await asyncio.to_thread(fetch_latest_player_id, db_path, character_id)
        if latest > baseline:
            logger.info(
                "新着プレイヤー発言を検知 (id=%s)、待機を打ち切ってループ先頭へ", latest
            )
            return True
    return False


def fetch_next_unplayed_ai(db_path: str, character_id: str) -> sqlite3.Row | None:
    with db_connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, speaker, content, created_at FROM messages "
            "WHERE character_id = ? AND speaker = 'ai' AND played = 0 "
            "ORDER BY id ASC LIMIT 1",
            (character_id,),
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


def mid_term_to_text(rows: list[sqlite3.Row]) -> str:
    """中期記憶を段落区切りのテキストに整形（古い順）。"""
    return "\n\n".join(r["summary"] for r in rows)


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


def call_summarize(
    client: genai.Client,
    model: str,
    template: str,
    history_text: str,
    target_chars: int,
    game_name: str,
) -> str:
    user_text = template.format(
        target_chars=target_chars,
        history_text=history_text,
        game_name=game_name,
    )
    response = client.models.generate_content(
        model=model,
        contents=[
            genai_types.Content(
                role="user",
                parts=[genai_types.Part.from_text(text=user_text)],
            )
        ],
    )
    return (response.text or "").strip()


def call_gemini(
    client: genai.Client,
    model: str,
    character_prompt: str,
    cheer_prompt: str,
    history_text: str,
    mid_term_text: str,
    image_path: Path,
) -> str:
    system_instruction = f"{character_prompt}\n\n---\n\n{cheer_prompt}"
    history_block = history_text if history_text else "（まだ会話履歴はありません）"
    mid_term_block = mid_term_text if mid_term_text else "（まだプレイ概要はありません）"
    user_text = (
        "直近の会話履歴（古い順）：\n"
        f"{history_block}\n\n"
        "ここまでのプレイの概要（古い順）：\n"
        f"{mid_term_block}\n\n"
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
    character_id = cfg["character"]["current_id"]
    current_day = app.state.current_day
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
        "メインループ開始: character=%s day=%s window_title=%r interval=%ss",
        character_id,
        current_day,
        window_title,
        interval,
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
                fetch_recent_history,
                db_path,
                character_id,
                history_count,
                current_day,
            )
            history_text = history_to_text(history_rows)

            mid_term_rows = await asyncio.to_thread(
                fetch_recent_mid_term_memories, db_path, character_id, 10
            )
            mid_term_text = mid_term_to_text(mid_term_rows)

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
                    mid_term_text,
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

            await asyncio.to_thread(
                insert_message,
                db_path,
                character_id,
                "ai",
                message,
                current_day,
                0,
            )
            logger.info(
                "AI発話を保存 (tier=%s, day=%s): %s",
                tier,
                current_day,
                message[:40],
            )

        except asyncio.CancelledError:
            logger.info("メインループ停止")
            raise
        except Exception:
            logger.exception("メインループで予期せぬ例外")

        await wait_for_new_player_message(db_path, character_id, interval * 3, 5)


async def mid_term_memory_loop(app: FastAPI) -> None:
    """中期記憶バッチループ。
    現在のキャラ・現在の day における mid_term_memories.last_message_id の
    最大値を取得し、それより新しい messages が batch_threshold 件以上溜まって
    いれば、直近 window_size 件（現 day 内）を flash モデルで要約して
    mid_term_memories に保存する。挿入時に対象レコードの最大 id を
    last_message_id として記録する。
    """
    cfg = app.state.config
    db_path = cfg["database"]["path"]
    character_id = cfg["character"]["current_id"]
    current_day = app.state.current_day
    mt_cfg = cfg["memory"]["mid_term"]
    window_size = mt_cfg["window_size"]
    target_chars = mt_cfg["target_chars"]
    batch_threshold = mt_cfg["batch_threshold"]
    interval = mt_cfg["interval_seconds"]
    flash_model = cfg["gemini"]["flash_model"]
    game_name = cfg["game"]["name"]
    summary_template = read_prompt(cfg["prompts"]["summary_path"])
    client: genai.Client = app.state.gemini_client

    logger.info(
        "中期記憶ループ開始: character=%s day=%s game=%s threshold=%d window=%d interval=%ss",
        character_id,
        current_day,
        game_name,
        batch_threshold,
        window_size,
        interval,
    )

    while True:
        try:
            last_msg_id = await asyncio.to_thread(
                fetch_mid_term_last_message_id,
                db_path,
                character_id,
                current_day,
            )
            new_count = await asyncio.to_thread(
                count_messages_after,
                db_path,
                character_id,
                current_day,
                last_msg_id,
            )
            if new_count >= batch_threshold:
                latest = await asyncio.to_thread(
                    fetch_max_message_id_today,
                    db_path,
                    character_id,
                    current_day,
                )
                logger.info(
                    "中期記憶バッチ実行 (day=%s, new=%d, last_msg_id=%d, latest=%d)",
                    current_day,
                    new_count,
                    last_msg_id,
                    latest,
                )
                history_rows = await asyncio.to_thread(
                    fetch_recent_history,
                    db_path,
                    character_id,
                    window_size,
                    current_day,
                )
                history_text = history_to_text(history_rows)
                try:
                    summary = await asyncio.to_thread(
                        call_summarize,
                        client,
                        flash_model,
                        summary_template,
                        history_text,
                        target_chars,
                        game_name,
                    )
                except Exception:
                    logger.exception("中期記憶の要約呼び出しに失敗")
                    await asyncio.sleep(interval)
                    continue
                if not summary:
                    logger.warning("要約結果が空でした (latest=%d)", latest)
                else:
                    await asyncio.to_thread(
                        insert_mid_term_memory,
                        db_path,
                        character_id,
                        summary,
                        current_day,
                        latest,
                    )
                    logger.info(
                        "中期記憶を保存 (day=%s, last_message_id=%d): %s",
                        current_day,
                        latest,
                        summary[:60],
                    )
        except asyncio.CancelledError:
            logger.info("中期記憶ループ停止")
            raise
        except Exception:
            logger.exception("中期記憶ループで予期せぬ例外")

        await asyncio.sleep(interval)


VALID_TIERS = ("flash", "pro")


def resolve_current_day(db_path: str, character_id: str) -> int:
    """起動時の current_day を決定する。
    1. HINALIVE_DAY が指定されていればそれを int として採用
    2. なければ messages から現キャラの最新 created_at 行の day を取得
    3. それもなければ 1
    """
    override = os.environ.get("HINALIVE_DAY")
    if override:
        try:
            day = int(override)
        except ValueError as e:
            raise ValueError(
                f"--day には整数を指定してください: {override!r}"
            ) from e
        logger.info("Day を起動オプションで指定: %d", day)
        return day
    latest = fetch_latest_day(db_path, character_id)
    if latest is not None:
        logger.info("Day を直近履歴から継続: %d (character=%s)", latest, character_id)
        return latest
    logger.info("Day を新規開始: 1 (character=%s)", character_id)
    return 1


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    app.state.config = cfg
    init_db(cfg["database"]["path"], cfg["character"]["current_id"])
    app.state.current_day = resolve_current_day(
        cfg["database"]["path"], cfg["character"]["current_id"]
    )
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

    main_task = asyncio.create_task(main_loop(app))
    mt_task = asyncio.create_task(mid_term_memory_loop(app))
    try:
        yield
    finally:
        for t in (main_task, mt_task):
            t.cancel()
        for t in (main_task, mt_task):
            try:
                await t
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
    character_id = cfg["character"]["current_id"]
    row = await asyncio.to_thread(
        fetch_next_unplayed_ai, cfg["database"]["path"], character_id
    )
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
    character_id = cfg["character"]["current_id"]
    current_day = app.state.current_day
    new_id = await asyncio.to_thread(
        insert_message,
        cfg["database"]["path"],
        character_id,
        "player",
        text,
        current_day,
        1,
    )
    return {"id": new_id}


FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="hinalive backend")
    parser.add_argument(
        "--cheer",
        help="cheer.mdの代わりに使うファイル名 (backend/prompts/ 配下)。拡張子省略可",
    )
    parser.add_argument(
        "--window",
        help="config.yaml の capture.window_title を上書きするウィンドウタイトル（部分一致）",
    )
    parser.add_argument(
        "--day",
        type=int,
        help="プレイ日 (Day) を整数で指定。1, 20260513 などどちらの運用も可。"
        "未指定時は直近履歴の day を継続、履歴が無ければ 1",
    )
    args = parser.parse_args()
    if args.cheer:
        os.environ["HINALIVE_CHEER_FILE"] = args.cheer
    if args.window:
        os.environ["HINALIVE_WINDOW_TITLE"] = args.window
    if args.day is not None:
        os.environ["HINALIVE_DAY"] = str(args.day)

    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
