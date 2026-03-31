from dotenv import load_dotenv
load_dotenv()

import logging
from fastapi import FastAPI, HTTPException, Depends

logger = logging.getLogger(__name__)
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
from jira import JiraConfig

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
                CREATE TABLE IF NOT EXISTS jira_order (
                    issue_key TEXT PRIMARY KEY,
                    sort_order INTEGER NOT NULL DEFAULT 0
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_jira_config (
                    email            TEXT PRIMARY KEY,
                    jira_base_url    TEXT NOT NULL DEFAULT '',
                    jira_email       TEXT NOT NULL DEFAULT '',
                    jira_api_token   TEXT NOT NULL DEFAULT '',
                    jira_project_key TEXT NOT NULL DEFAULT '',
                    jira_parent_key  TEXT NOT NULL DEFAULT '',
                    jira_board_id    TEXT NOT NULL DEFAULT '',
                    jira_temp_key    TEXT NOT NULL DEFAULT '',
                    ticket_mode      TEXT NOT NULL DEFAULT 'SUBTASK'
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


# ── Per-user Jira config ───────────────────────────────────────────────────────

def get_user_jira_cfg(email: str) -> "JiraConfig | None":
    """Return JiraConfig from DB for this user, or None if not configured."""
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM user_jira_config WHERE email = %s", (email,))
                row = cur.fetchone()
        if row and row["jira_api_token"]:
            return JiraConfig(
                base_url=row["jira_base_url"].rstrip("/"),
                email=row["jira_email"],
                api_token=row["jira_api_token"],
                project_key=row["jira_project_key"],
                parent_key=row["jira_parent_key"],
                board_id=row["jira_board_id"],
                temp_key=row["jira_temp_key"],
                ticket_mode=(row["ticket_mode"] or "SUBTASK").upper(),
            )
    except Exception:
        pass
    return None


def require_jira_cfg(user) -> JiraConfig:
    """Get Jira config for user from DB only. Raises 400 if unconfigured."""
    cfg = get_user_jira_cfg(user["email"])
    if cfg:
        return cfg
    raise HTTPException(
        status_code=400,
        detail="JIRA_NOT_CONFIGURED"
    )


# ── Settings ──────────────────────────────────────────────────────────────────

@app.get("/settings/jira")
def get_jira_settings(user=Depends(require_auth)):
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM user_jira_config WHERE email = %s", (user["email"],))
                row = cur.fetchone()
    except Exception:
        row = None
    if not row:
        return {"configured": False}
    return {
        "configured":       bool(row["jira_api_token"]),
        "jira_base_url":    row["jira_base_url"],
        "jira_email":       row["jira_email"],
        "jira_api_token":   "••••••••" if row["jira_api_token"] else "",
        "jira_project_key": row["jira_project_key"],
        "jira_parent_key":  row["jira_parent_key"],
        "jira_board_id":    row["jira_board_id"],
        "jira_temp_key":    row["jira_temp_key"],
        "ticket_mode":      row["ticket_mode"],
    }


class JiraSettingsRequest(BaseModel):
    jira_base_url: str
    jira_email: str
    jira_api_token: str = ""
    jira_project_key: str = ""
    jira_parent_key: str = ""
    jira_board_id: str = ""
    jira_temp_key: str = ""
    ticket_mode: str = "SUBTASK"


@app.post("/settings/jira/test")
def test_jira_settings(body: JiraSettingsRequest, user=Depends(require_auth)):
    """Test Jira credentials without saving. Returns project name on success."""
    token = body.jira_api_token.strip()
    if not token or token == "••••••••":
        saved = get_user_jira_cfg(user["email"])
        token = saved.api_token if saved else ""
    if not token:
        raise HTTPException(status_code=400, detail="API Token이 필요합니다.")
    cfg = JiraConfig(
        base_url=body.jira_base_url.rstrip("/"),
        email=body.jira_email,
        api_token=token,
        project_key=body.jira_project_key,
    )
    import httpx as _httpx
    try:
        res = _httpx.get(
            f"{cfg.base_url}/rest/api/3/project/{cfg.project_key}",
            auth=cfg.auth,
            timeout=10,
        )
        if res.status_code == 200:
            return {"ok": True, "project_name": res.json().get("name", cfg.project_key)}
        raise HTTPException(status_code=400, detail=f"Jira 응답 오류: {res.status_code}")
    except _httpx.RequestError as e:
        raise HTTPException(status_code=400, detail=f"연결 오류: {e}")


@app.put("/settings/jira")
def save_jira_settings(body: JiraSettingsRequest, user=Depends(require_auth)):
    new_token = body.jira_api_token.strip()
    keep_existing = not new_token or new_token == "••••••••"

    with get_db() as conn:
        with conn.cursor() as cur:
            if keep_existing:
                cur.execute("""
                    INSERT INTO user_jira_config
                        (email, jira_base_url, jira_email, jira_api_token,
                         jira_project_key, jira_parent_key, jira_board_id, jira_temp_key, ticket_mode)
                    VALUES (%s,%s,%s,'',%s,%s,%s,%s,%s)
                    ON CONFLICT (email) DO UPDATE SET
                        jira_base_url    = EXCLUDED.jira_base_url,
                        jira_email       = EXCLUDED.jira_email,
                        jira_project_key = EXCLUDED.jira_project_key,
                        jira_parent_key  = EXCLUDED.jira_parent_key,
                        jira_board_id    = EXCLUDED.jira_board_id,
                        jira_temp_key    = EXCLUDED.jira_temp_key,
                        ticket_mode      = EXCLUDED.ticket_mode
                """, (user["email"], body.jira_base_url, body.jira_email,
                      body.jira_project_key, body.jira_parent_key,
                      body.jira_board_id, body.jira_temp_key, body.ticket_mode))
            else:
                cur.execute("""
                    INSERT INTO user_jira_config
                        (email, jira_base_url, jira_email, jira_api_token,
                         jira_project_key, jira_parent_key, jira_board_id, jira_temp_key, ticket_mode)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (email) DO UPDATE SET
                        jira_base_url    = EXCLUDED.jira_base_url,
                        jira_email       = EXCLUDED.jira_email,
                        jira_api_token   = EXCLUDED.jira_api_token,
                        jira_project_key = EXCLUDED.jira_project_key,
                        jira_parent_key  = EXCLUDED.jira_parent_key,
                        jira_board_id    = EXCLUDED.jira_board_id,
                        jira_temp_key    = EXCLUDED.jira_temp_key,
                        ticket_mode      = EXCLUDED.ticket_mode
                """, (user["email"], body.jira_base_url, body.jira_email, new_token,
                      body.jira_project_key, body.jira_parent_key,
                      body.jira_board_id, body.jira_temp_key, body.ticket_mode))
        conn.commit()
    return {"ok": True}


# ── Print jobs ────────────────────────────────────────────────────────────────

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
        cfg = get_user_jira_cfg(user["email"])
        jira_client.mark_done(row[0], cfg)

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
        cfg = get_user_jira_cfg(user["email"])
        jira_client.delete_issue(row[0], cfg)

    return {"ok": True}


class ReorderRequest(BaseModel):
    ids: list[int]


@app.post("/jobs/sync-jira")
def sync_jobs_from_jira(user=Depends(require_auth)):
    """Pull items from Jira and upsert into print_jobs.
    New items are inserted; existing ones have their status updated.
    Jira statusCategory=done → printer 'done', otherwise 'progress'.
    """
    cfg = get_user_jira_cfg(user["email"])
    if not cfg:
        raise HTTPException(status_code=400, detail="JIRA_NOT_CONFIGURED")

    try:
        items = jira_client.get_printer_items(cfg)
    except Exception as e:
        logger.error("sync-jira fetch error: %s", e)
        raise HTTPException(status_code=500, detail=f"Jira 조회 실패: {e}")

    inserted = updated = 0
    now_kst = datetime.now(KST)

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT jira_key, status FROM print_jobs"
                " WHERE printed_by = %s AND jira_key IS NOT NULL",
                (user["email"],),
            )
            existing = {row["jira_key"]: row["status"] for row in cur.fetchall()}

        with conn.cursor() as cur:
            for item in items:
                jira_key  = item["key"]
                new_status = "done" if item["status_done"] else "progress"
                completed  = now_kst if new_status == "done" else None

                if jira_key in existing:
                    if existing[jira_key] != new_status:
                        cur.execute(
                            "UPDATE print_jobs SET status=%s, completed_at=%s"
                            " WHERE jira_key=%s AND printed_by=%s",
                            (new_status, completed, jira_key, user["email"]),
                        )
                        updated += 1
                else:
                    cur.execute(
                        "INSERT INTO print_jobs"
                        " (title, printed_by, status, sort_order, jira_key, completed_at)"
                        " VALUES (%s, %s, %s,"
                        "  COALESCE((SELECT MIN(sort_order)-1 FROM print_jobs p2"
                        "            WHERE p2.printed_by=%s AND p2.status='progress'), 0),"
                        "  %s, %s)",
                        (item["summary"], user["email"], new_status,
                         user["email"], jira_key, completed),
                    )
                    inserted += 1
        conn.commit()

    return {"inserted": inserted, "updated": updated, "total": len(items)}


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


# ── Jira endpoints ────────────────────────────────────────────────────────────

class AssignEpicRequest(BaseModel):
    epic_key: "str | None" = None


@app.get("/jira/epics-tasks")
def get_epics_tasks(user=Depends(require_auth)):
    return jira_client.get_epics_and_tasks(require_jira_cfg(user))


@app.get("/jira/epics-tasks-ordered")
def get_epics_tasks_ordered(user=Depends(require_auth)):
    data = jira_client.get_epics_and_tasks(require_jira_cfg(user))
    all_keys = [i["key"] for i in data["epics"] + data["tasks"]]
    if all_keys:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT issue_key, sort_order FROM jira_order WHERE issue_key = ANY(%s)",
                    (all_keys,)
                )
                order_map = {row[0]: row[1] for row in cur.fetchall()}
        data["epics"] = sorted(data["epics"], key=lambda i: order_map.get(i["key"], 999999))
        data["tasks"] = sorted(data["tasks"], key=lambda i: order_map.get(i["key"], 999999))
    return data


@app.patch("/jira/tasks/{task_key}/epic")
def assign_task_epic(task_key: str, body: AssignEpicRequest, user=Depends(require_auth)):
    if not jira_client.assign_task_to_epic(task_key, body.epic_key, require_jira_cfg(user)):
        raise HTTPException(status_code=500, detail="Failed to update Jira")
    return {"ok": True}


@app.get("/jira/tasks-subtasks")
def get_tasks_subtasks(user=Depends(require_auth)):
    return jira_client.get_tasks_and_subtasks(require_jira_cfg(user))


@app.get("/jira/issues/{issue_key}/transitions")
def get_issue_transitions(issue_key: str, user=Depends(require_auth)):
    return jira_client.get_transitions(issue_key, require_jira_cfg(user))


class TransitionRequest(BaseModel):
    transition_id: str


@app.post("/jira/issues/{issue_key}/transitions")
def apply_issue_transition(issue_key: str, body: TransitionRequest, user=Depends(require_auth)):
    if not jira_client.apply_transition(issue_key, body.transition_id, require_jira_cfg(user)):
        raise HTTPException(status_code=500, detail="Failed to apply transition")
    return {"ok": True}


class AssignParentRequest(BaseModel):
    task_key: str


@app.get("/jira/all-items")
def get_all_items(user=Depends(require_auth)):
    data = jira_client.get_all_items(require_jira_cfg(user))
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
    item = jira_client.create_epic(body.title, require_jira_cfg(user))
    if not item:
        raise HTTPException(status_code=500, detail="Failed to create epic")
    return item


@app.post("/jira/tasks")
def create_task(body: CreateItemRequest, user=Depends(require_auth)):
    item = jira_client.create_task_item(body.title, require_jira_cfg(user))
    if not item:
        raise HTTPException(status_code=500, detail="Failed to create task")
    return item


@app.post("/jira/subtasks")
def create_subtask(body: CreateItemRequest, user=Depends(require_auth)):
    item = jira_client.create_subtask_item(body.title, require_jira_cfg(user))
    if not item:
        raise HTTPException(status_code=500, detail="Failed to create subtask")
    return item


class UpdateSummaryRequest(BaseModel):
    summary: str


@app.patch("/jira/issues/{issue_key}/summary")
def update_issue_summary(issue_key: str, body: UpdateSummaryRequest, user=Depends(require_auth)):
    if not jira_client.update_summary(issue_key, body.summary, require_jira_cfg(user)):
        raise HTTPException(status_code=500, detail="Failed to update summary")
    return {"ok": True}


class SetDueDateRequest(BaseModel):
    due_date: "str | None" = None


@app.patch("/jira/issues/{issue_key}/due-date")
def set_issue_due_date(issue_key: str, body: SetDueDateRequest, user=Depends(require_auth)):
    if not jira_client.set_due_date(issue_key, body.due_date, require_jira_cfg(user)):
        raise HTTPException(status_code=500, detail="Failed to set due date")
    return {"ok": True}


@app.post("/jira/issues/{issue_key}/done")
def done_jira_issue(issue_key: str, user=Depends(require_auth)):
    if not jira_client.mark_done_issue(issue_key, require_jira_cfg(user)):
        raise HTTPException(status_code=500, detail="Failed to mark done")
    return {"ok": True}


@app.delete("/jira/issues/{issue_key}")
def delete_jira_issue(issue_key: str, user=Depends(require_auth)):
    if not jira_client.delete_issue(issue_key, require_jira_cfg(user)):
        raise HTTPException(status_code=500, detail="Failed to delete issue")
    return {"ok": True}


@app.patch("/jira/subtasks/{subtask_key}/parent")
def assign_subtask_parent(subtask_key: str, body: AssignParentRequest, user=Depends(require_auth)):
    if not jira_client.assign_subtask_to_task(subtask_key, body.task_key, require_jira_cfg(user)):
        raise HTTPException(status_code=500, detail="Failed to update Jira")
    return {"ok": True}


# ── Print ─────────────────────────────────────────────────────────────────────

class PrintRequest(BaseModel):
    title: str
    print_enabled: bool = True

class ReprintRequest(BaseModel):
    print_enabled: bool = True


@app.post("/api/print")
def print_receipt(body: PrintRequest, user=Depends(require_auth)):
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")

    p = None
    try:
        if body.print_enabled:
            now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
            lines = [
                (title,    FONT_SIZE_TITLE, True),
                ("─" * 28, FONT_SIZE_BODY,  True),
                (now,      FONT_SIZE_SMALL, True),
            ]
            img = _text_to_image(lines)
            p = File(DEVICE)
            p.image(img)
            p.text("\n")
            p.cut()

        # Create Jira issue for any user who has Jira configured in DB
        cfg = get_user_jira_cfg(user["email"])
        jira_key = jira_client.create_issue(title, cfg) if cfg else None

        with get_db() as conn:
            with conn.cursor() as cur:
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


@app.post("/jobs/{job_id}/reprint")
def reprint_job(job_id: int, body: ReprintRequest, user=Depends(require_auth)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT title FROM print_jobs WHERE id=%s AND printed_by=%s",
                (job_id, user["email"])
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    title = row[0]
    p = None
    try:
        if body.print_enabled:
            now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
            lines = [
                (title,    FONT_SIZE_TITLE, True),
                ("─" * 28, FONT_SIZE_BODY,  True),
                (now,      FONT_SIZE_SMALL, True),
            ]
            img = _text_to_image(lines)
            p = File(DEVICE)
            p.image(img)
            p.text("\n")
            p.cut()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if p:
            p.close()
