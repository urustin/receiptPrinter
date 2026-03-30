from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel
from escpos.printer import File
from datetime import datetime, timezone, timedelta
from PIL import Image, ImageDraw, ImageFont
import textwrap
import glob
import os
import psycopg2
from psycopg2.extras import RealDictCursor

from auth import router as auth_router, require_auth
import jira as jira_client

KST = timezone(timedelta(hours=9))

app = FastAPI()

app.add_middleware(SessionMiddleware, secret_key=os.environ["SECRET_KEY"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)


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
            cur.execute("""
                ALTER TABLE print_jobs
                ADD COLUMN IF NOT EXISTS printed_by TEXT NOT NULL DEFAULT ''
            """)
            cur.execute("""
                ALTER TABLE print_jobs
                ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'done'
            """)
            cur.execute("""
                ALTER TABLE print_jobs
                ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ
            """)
            cur.execute("""
                ALTER TABLE print_jobs
                ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0
            """)
            cur.execute("""
                ALTER TABLE print_jobs
                ADD COLUMN IF NOT EXISTS jira_key TEXT
            """)
            cur.execute("""
                UPDATE print_jobs SET sort_order = id WHERE sort_order = 0
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS jira_order (
                    issue_key TEXT PRIMARY KEY,
                    sort_order INTEGER NOT NULL DEFAULT 0
                )
            """)
        conn.commit()


init_db()
DEVICE = "/dev/usb/lp0"
PAPER_PX = 576
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


@app.get("/history")
def history(user=Depends(require_auth)):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, title, status,"
                " to_char(printed_at AT TIME ZONE 'Asia/Seoul', 'YYYY-MM-DD HH24:MI') AS printed_at,"
                " to_char(completed_at AT TIME ZONE 'Asia/Seoul', 'YYYY-MM-DD HH24:MI') AS completed_at"
                " FROM print_jobs WHERE printed_by = %s"
                " ORDER BY CASE WHEN status='progress' THEN sort_order ELSE NULL END ASC NULLS LAST,"
                " printed_at DESC LIMIT 100",
                (user["email"],),
            )
            rows = cur.fetchall()
    items = [dict(r) for r in rows]
    return {
        "progress": [r for r in items if r["status"] == "progress"],
        "done":     [r for r in items if r["status"] == "done"],
    }


@app.patch("/jobs/{job_id}/done")
def mark_done(job_id: int, user=Depends(require_auth)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE print_jobs SET status='done', completed_at=now() WHERE id=%s AND printed_by=%s"
                " RETURNING jira_key",
                (job_id, user["email"]),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Not found")
            row = cur.fetchone()
        conn.commit()

    if row and row[0]:
        jira_client.mark_done(row[0])

    return {"ok": True}


@app.delete("/jobs/{job_id}")
def delete_job(job_id: int, user=Depends(require_auth)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM print_jobs WHERE id=%s AND printed_by=%s RETURNING jira_key",
                (job_id, user["email"]),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Not found")
            row = cur.fetchone()
        conn.commit()

    if row and row[0]:
        jira_client.delete_issue(row[0])

    return {"ok": True}


class ReorderRequest(BaseModel):
    ids: list[int]


@app.patch("/jobs/reorder")
def reorder_jobs(body: ReorderRequest, user=Depends(require_auth)):
    with get_db() as conn:
        with conn.cursor() as cur:
            for order, job_id in enumerate(body.ids):
                cur.execute(
                    "UPDATE print_jobs SET sort_order=%s WHERE id=%s AND printed_by=%s AND status='progress'",
                    (order, job_id, user["email"]),
                )
        conn.commit()
    return {"ok": True}


class AssignEpicRequest(BaseModel):
    epic_key: str | None = None


@app.get("/jira/epics-tasks")
def get_epics_tasks(user=Depends(require_auth)):
    return jira_client.get_epics_and_tasks()


@app.patch("/jira/tasks/{task_key}/epic")
def assign_task_epic(task_key: str, body: AssignEpicRequest, user=Depends(require_auth)):
    ok = jira_client.assign_task_to_epic(task_key, body.epic_key)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update Jira")
    return {"ok": True}


@app.get("/jira/tasks-subtasks")
def get_tasks_subtasks(user=Depends(require_auth)):
    return jira_client.get_tasks_and_subtasks()


@app.get("/jira/issues/{issue_key}/transitions")
def get_issue_transitions(issue_key: str, user=Depends(require_auth)):
    return jira_client.get_transitions(issue_key)


class TransitionRequest(BaseModel):
    transition_id: str


@app.post("/jira/issues/{issue_key}/transitions")
def apply_issue_transition(issue_key: str, body: TransitionRequest, user=Depends(require_auth)):
    ok = jira_client.apply_transition(issue_key, body.transition_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to apply transition")
    return {"ok": True}


class AssignParentRequest(BaseModel):
    task_key: str


@app.get("/jira/all-items")
def get_all_items(user=Depends(require_auth)):
    data = jira_client.get_all_items()
    all_keys = [i["key"] for i in data["epics"] + data["tasks"] + data["subtasks"]]
    if all_keys:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT issue_key, sort_order FROM jira_order WHERE issue_key = ANY(%s)",
                    (all_keys,)
                )
                order_map = {row[0]: row[1] for row in cur.fetchall()}
        for col in ("epics", "tasks", "subtasks"):
            data[col] = sorted(data[col], key=lambda i: order_map.get(i["key"], 999999))
    return data


class SaveOrderRequest(BaseModel):
    keys: list[str]


@app.post("/jira/order")
def save_jira_order(body: SaveOrderRequest, user=Depends(require_auth)):
    with get_db() as conn:
        with conn.cursor() as cur:
            for order, key in enumerate(body.keys):
                cur.execute(
                    "INSERT INTO jira_order (issue_key, sort_order) VALUES (%s, %s)"
                    " ON CONFLICT (issue_key) DO UPDATE SET sort_order = EXCLUDED.sort_order",
                    (key, order)
                )
        conn.commit()
    return {"ok": True}


class CreateItemRequest(BaseModel):
    title: str


@app.post("/jira/epics")
def create_epic(body: CreateItemRequest, user=Depends(require_auth)):
    item = jira_client.create_epic(body.title)
    if not item:
        raise HTTPException(status_code=500, detail="Failed to create epic")
    return item


@app.post("/jira/tasks")
def create_task(body: CreateItemRequest, user=Depends(require_auth)):
    item = jira_client.create_task_item(body.title)
    if not item:
        raise HTTPException(status_code=500, detail="Failed to create task")
    return item


@app.post("/jira/subtasks")
def create_subtask(body: CreateItemRequest, user=Depends(require_auth)):
    item = jira_client.create_subtask_item(body.title)
    if not item:
        raise HTTPException(status_code=500, detail="Failed to create subtask")
    return item


class UpdateSummaryRequest(BaseModel):
    summary: str


@app.patch("/jira/issues/{issue_key}/summary")
def update_issue_summary(issue_key: str, body: UpdateSummaryRequest, user=Depends(require_auth)):
    ok = jira_client.update_summary(issue_key, body.summary)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update summary")
    return {"ok": True}


class SetDueDateRequest(BaseModel):
    due_date: str | None = None


@app.patch("/jira/issues/{issue_key}/due-date")
def set_issue_due_date(issue_key: str, body: SetDueDateRequest, user=Depends(require_auth)):
    ok = jira_client.set_due_date(issue_key, body.due_date)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to set due date")
    return {"ok": True}


@app.post("/jira/issues/{issue_key}/done")
def done_jira_issue(issue_key: str, user=Depends(require_auth)):
    ok = jira_client.mark_done_issue(issue_key)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to mark done")
    return {"ok": True}


@app.delete("/jira/issues/{issue_key}")
def delete_jira_issue(issue_key: str, user=Depends(require_auth)):
    ok = jira_client.delete_issue(issue_key)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to delete issue")
    return {"ok": True}


@app.patch("/jira/subtasks/{subtask_key}/parent")
def assign_subtask_parent(subtask_key: str, body: AssignParentRequest, user=Depends(require_auth)):
    ok = jira_client.assign_subtask_to_task(subtask_key, body.task_key)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update Jira")
    return {"ok": True}


class PrintRequest(BaseModel):
    title: str


@app.post("/print")
def print_receipt(body: PrintRequest, user=Depends(require_auth)):
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")

    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
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
                jira_key = jira_client.create_issue(title) if user["email"] == "urustin@gmail.com" else None
                cur.execute(
                    "INSERT INTO print_jobs (title, printed_by, status, sort_order, jira_key)"
                    " VALUES (%s, %s, 'progress',"
                    "  COALESCE((SELECT MIN(sort_order)-1 FROM print_jobs WHERE printed_by=%s AND status='progress'), 0),"
                    "  %s)",
                    (title, user["email"], user["email"], jira_key),
                )
            conn.commit()

        return {"ok": True}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        if p:
            p.close()
