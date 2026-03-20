from flask import Flask, request, jsonify, render_template
from escpos.printer import File
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
from PIL import Image, ImageDraw, ImageFont
import textwrap
import glob
import os
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)


def get_db():
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=os.environ.get("DB_PORT", 5432),
        dbname=os.environ.get("DB_NAME", "printer"),
        user=os.environ.get("DB_USER", "printer"),
        password=os.environ.get("DB_PASSWORD", "printer"),
    )


def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS print_jobs (
                    id        SERIAL PRIMARY KEY,
                    title     TEXT NOT NULL,
                    printed_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
        conn.commit()


init_db()
DEVICE = "/dev/usb/lp0"
PAPER_PX = 576       # 80mm paper at 203dpi
FONT_SIZE_TITLE = 36
FONT_SIZE_BODY  = 28
FONT_SIZE_SMALL = 24


def _find_korean_font():
    patterns = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK*.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK*.ttc",
        "/usr/share/fonts/**/Noto*CJK*.ttc",
        "/usr/share/fonts/**/Noto*CJK*.otf",
    ]
    for p in patterns:
        hits = glob.glob(p, recursive=True)
        if hits:
            return hits[0]
    raise FileNotFoundError("Korean font not found")


FONT_PATH = _find_korean_font()


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size)


def _text_to_image(lines: list[tuple[str, int, bool]]) -> Image.Image:
    """
    lines: list of (text, font_size, center_align)
    Returns a white Image ready to send to the printer.
    """
    pad = 12
    entries = []
    for text, size, center in lines:
        font = _font(size)
        wrap_width = int((PAPER_PX - pad * 2) / (size * 0.6))
        wrapped = textwrap.fill(text, width=max(wrap_width, 10))
        entries.append((wrapped, font, center))

    dummy = Image.new("L", (PAPER_PX, 1))
    draw = ImageDraw.Draw(dummy)
    total_h = pad
    for text, font, _ in entries:
        bbox = draw.multiline_textbbox((0, 0), text, font=font)
        total_h += bbox[3] - bbox[1] + 8
    total_h += pad

    img = Image.new("L", (PAPER_PX, total_h), 255)
    draw = ImageDraw.Draw(img)
    y = pad
    for text, font, center in entries:
        bbox = draw.multiline_textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        x = (PAPER_PX - w) // 2 if center else pad
        draw.multiline_text((x, y), text, font=font, fill=0)
        y += bbox[3] - bbox[1] + 8

    return img


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/history")
def history():
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT title, to_char(printed_at, 'YYYY-MM-DD HH24:MI') AS printed_at"
                " FROM print_jobs ORDER BY printed_at DESC LIMIT 50"
            )
            rows = cur.fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/print", methods=["POST"])
def print_receipt():
    data = request.get_json(force=True, silent=True) or {}
    title = (data.get("title") or "").strip()
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    if not title:
        return jsonify({"error": "Title is required"}), 400

    lines = [
        (title,    FONT_SIZE_TITLE, True),
        ("─" * 28, FONT_SIZE_BODY,  True),
        (now,      FONT_SIZE_SMALL, True),
    ]

    img = _text_to_image(lines)

    p = None
    try:
        p = File(DEVICE)
        p.image(img)
        p.text("\n")
        p.cut()

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO print_jobs (title) VALUES (%s)",
                    (title,)
                )
            conn.commit()

        return jsonify({"ok": True})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        if p:
            p.close()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
