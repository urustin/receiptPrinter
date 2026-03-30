import os
import logging
import httpx

logger = logging.getLogger(__name__)

_BASE   = os.environ["JIRA_BASE_URL"].rstrip("/")   # https://flaresolution2.atlassian.net
_EMAIL  = os.environ["JIRA_EMAIL"]
_TOKEN  = os.environ["JIRA_API_TOKEN"]
_AUTH   = (_EMAIL, _TOKEN)

# TICKET=SUBTASK  → create subtask under JIRA_PARENT_KEY (default)
# TICKET=TASK     → create independent task in JIRA_PROJECT_KEY (appears in board TO DO)
_TICKET_MODE = os.environ.get("TICKET", "SUBTASK").upper()
_PARENT      = os.environ.get("JIRA_PARENT_KEY", "")   # required for SUBTASK mode
_PROJECT_KEY = os.environ.get("JIRA_PROJECT_KEY") or (_PARENT.split("-")[0] if _PARENT else "")
_BOARD_ID    = os.environ.get("JIRA_BOARD_ID", "")     # required for TASK mode (sprint board)
_TEMP_KEY    = os.environ.get("JIRA_TEMP_KEY", "")     # task that acts as "unassigned" holder for subtasks

SUBTASK_TYPE_ID = "10002"
DONE_TRANSITION = "51"



def _move_to_board(issue_key: str) -> None:
    """Move an issue from backlog to the board."""
    try:
        httpx.post(
            f"{_BASE}/rest/agile/1.0/board/{_BOARD_ID}/issue",
            auth=_AUTH,
            json={"issues": [issue_key]},
            timeout=10,
        )
    except Exception as e:
        logger.error("_move_to_board error: %s", e)


def _transition_to_todo(issue_key: str) -> None:
    """Fetch available transitions and apply the first one named 'To Do' (case-insensitive)."""
    try:
        res = httpx.get(
            f"{_BASE}/rest/api/3/issue/{issue_key}/transitions",
            auth=_AUTH,
            timeout=10,
        )
        res.raise_for_status()
        transitions = res.json().get("transitions", [])
        logger.info("transitions for %s: %s", issue_key, [(t["id"], t["to"]["name"]) for t in transitions])
        todo = next(
            (t for t in transitions if t["to"]["name"].lower() == "to do"),
            None,
        )
        if todo:
            r2 = httpx.post(
                f"{_BASE}/rest/api/3/issue/{issue_key}/transitions",
                auth=_AUTH,
                json={"transition": {"id": todo["id"]}},
                timeout=10,
            )
            logger.info("transition to To Do: status=%s", r2.status_code)
        else:
            logger.warning("No 'To Do' transition found for %s", issue_key)
    except Exception as e:
        logger.error("_transition_to_todo error: %s", e)


def create_issue(title: str) -> str | None:
    """Create a Jira issue (subtask or task) depending on TICKET env var. Returns issue key or None."""
    try:
        if _TICKET_MODE == "TASK":
            fields = {
                "project":   {"key": _PROJECT_KEY},
                "summary":   title,
                "issuetype": {"name": "Task"},
            }
        else:
            fields = {
                "project":   {"key": _PROJECT_KEY},
                "parent":    {"key": _PARENT},
                "summary":   title,
                "issuetype": {"id": SUBTASK_TYPE_ID},
            }

        res = httpx.post(
            f"{_BASE}/rest/api/3/issue",
            auth=_AUTH,
            json={"fields": fields},
            timeout=10,
        )
        res.raise_for_status()
        issue_key = res.json()["key"]

        if _TICKET_MODE == "TASK":
            _move_to_board(issue_key)
            _transition_to_todo(issue_key)

        return issue_key
    except Exception:
        return None


def delete_issue(issue_key: str) -> bool:
    """Delete a Jira issue. Returns True on success."""
    try:
        res = httpx.delete(
            f"{_BASE}/rest/api/3/issue/{issue_key}",
            auth=_AUTH,
            timeout=10,
        )
        return res.status_code == 204
    except Exception:
        return False


def mark_done(issue_key: str) -> bool:
    """Transition a Jira issue to Done. Returns True on success."""
    try:
        res = httpx.post(
            f"{_BASE}/rest/api/3/issue/{issue_key}/transitions",
            auth=_AUTH,
            json={"transition": {"id": DONE_TRANSITION}},
            timeout=10,
        )
        return res.status_code == 204
    except Exception:
        return False


def _search(jql: str, fields: str, max_results: int = 100) -> list:
    issues = []
    start = 0
    while True:
        try:
            res = httpx.get(
                f"{_BASE}/rest/api/3/search/jql",
                auth=_AUTH,
                params={"jql": jql, "fields": fields, "maxResults": max_results, "startAt": start},
                timeout=15,
            )
            res.raise_for_status()
            data = res.json()
            batch = data.get("issues", [])
            issues.extend(batch)
            if start + len(batch) >= data.get("total", 0) or not batch:
                break
            start += len(batch)
        except Exception as e:
            logger.error("_search error jql=%s: %s", jql, e)
            break
    return issues


def get_epics_and_tasks() -> dict:
    """Fetch all epics and tasks from the project. Returns {epics, tasks}."""
    epics_raw = _search(
        f"project={_PROJECT_KEY} AND issuetype=Epic ORDER BY created DESC",
        "summary,status",
    )
    epics = [
        {
            "key": i["key"],
            "summary": i["fields"]["summary"],
            "status": i["fields"]["status"]["name"],
        }
        for i in epics_raw
    ]

    tasks_raw = _search(
        f"project={_PROJECT_KEY} AND issuetype=Task ORDER BY created DESC",
        "summary,status,parent,customfield_10014",
    )
    tasks = []
    for i in tasks_raw:
        fields = i["fields"]
        epic_key = None
        parent = fields.get("parent")
        if parent:
            ptype = parent.get("fields", {}).get("issuetype", {}).get("name", "")
            if ptype == "Epic":
                epic_key = parent["key"]
        if epic_key is None:
            cf = fields.get("customfield_10014")
            if cf:
                epic_key = cf
        tasks.append({
            "key": i["key"],
            "summary": fields["summary"],
            "status": fields["status"]["name"],
            "epic_key": epic_key,
        })

    return {"epics": epics, "tasks": tasks}


def assign_task_to_epic(task_key: str, epic_key: str | None) -> bool:
    """Assign (or unassign) a task to an epic. Returns True on success."""
    try:
        if epic_key:
            payload = {"fields": {"parent": {"key": epic_key}}}
        else:
            payload = {"fields": {"parent": None}}

        res = httpx.put(
            f"{_BASE}/rest/api/3/issue/{task_key}",
            auth=_AUTH,
            json=payload,
            timeout=10,
        )
        if res.status_code == 204:
            return True

        # Fallback: classic projects use customfield_10014 for epic link
        payload2 = {"fields": {"customfield_10014": epic_key}}
        res2 = httpx.put(
            f"{_BASE}/rest/api/3/issue/{task_key}",
            auth=_AUTH,
            json=payload2,
            timeout=10,
        )
        if res2.status_code == 204:
            return True

        logger.error("assign_task_to_epic failed: task=%s epic=%s status=%s body=%s",
                     task_key, epic_key, res.status_code, res.text[:200])
        return False
    except Exception as e:
        logger.error("assign_task_to_epic error: %s", e)
        return False


def get_tasks_and_subtasks() -> dict:
    """Fetch all tasks and subtasks. Returns {tasks, subtasks, temp_key}."""
    tasks_raw = _search(
        f"project={_PROJECT_KEY} AND issuetype=Task ORDER BY created DESC",
        "summary,status",
    )
    tasks = [
        {
            "key": i["key"],
            "summary": i["fields"]["summary"],
            "status": i["fields"]["status"]["name"],
        }
        for i in tasks_raw
    ]

    subtasks_raw = _search(
        f"project={_PROJECT_KEY} AND issuetype=Subtask ORDER BY created DESC",
        "summary,status,parent",
    )
    subtasks = []
    for i in subtasks_raw:
        parent = i["fields"].get("parent")
        subtasks.append({
            "key": i["key"],
            "summary": i["fields"]["summary"],
            "status": i["fields"]["status"]["name"],
            "parent_key": parent["key"] if parent else None,
        })

    # Resolve temp_key: env var → task named "TEMP" → JIRA_PARENT_KEY
    temp_key = _TEMP_KEY
    if not temp_key:
        temp_task = next((t for t in tasks if t["summary"].strip().upper() == "TEMP"), None)
        temp_key = temp_task["key"] if temp_task else _PARENT

    return {"tasks": tasks, "subtasks": subtasks, "temp_key": temp_key}


def get_transitions(issue_key: str) -> list:
    """Return available transitions for an issue."""
    try:
        res = httpx.get(
            f"{_BASE}/rest/api/3/issue/{issue_key}/transitions",
            auth=_AUTH,
            timeout=10,
        )
        res.raise_for_status()
        return [
            {"id": t["id"], "name": t["to"]["name"]}
            for t in res.json().get("transitions", [])
        ]
    except Exception as e:
        logger.error("get_transitions error: %s", e)
        return []


def apply_transition(issue_key: str, transition_id: str) -> bool:
    """Apply a transition to an issue. Returns True on success."""
    try:
        res = httpx.post(
            f"{_BASE}/rest/api/3/issue/{issue_key}/transitions",
            auth=_AUTH,
            json={"transition": {"id": transition_id}},
            timeout=10,
        )
        return res.status_code == 204
    except Exception as e:
        logger.error("apply_transition error: %s", e)
        return False


def assign_subtask_to_task(subtask_key: str, task_key: str) -> bool:
    """Reassign a subtask to a different parent task. Returns True on success."""
    try:
        res = httpx.put(
            f"{_BASE}/rest/api/3/issue/{subtask_key}",
            auth=_AUTH,
            json={"fields": {"parent": {"key": task_key}}},
            timeout=10,
        )
        if res.status_code == 204:
            return True
        logger.error("assign_subtask_to_task failed: subtask=%s task=%s status=%s body=%s",
                     subtask_key, task_key, res.status_code, res.text[:200])
        return False
    except Exception as e:
        logger.error("assign_subtask_to_task error: %s", e)
        return False


# ── manage_task helpers ────────────────────────────────────────────────────────

def _fmt_issue(i: dict, include_parent: bool = False) -> dict:
    f = i["fields"]
    d = {
        "key":         i["key"],
        "summary":     f["summary"],
        "status":      f["status"]["name"],
        "status_done": f["status"].get("statusCategory", {}).get("key") == "done",
        "due_date":    f.get("duedate"),
    }
    if include_parent:
        p = f.get("parent")
        d["parent_key"] = p["key"] if p else None
    return d


def get_all_items() -> dict:
    """Fetch epics, tasks, and subtasks with due dates."""
    epics_raw    = _search(f"project={_PROJECT_KEY} AND issuetype=Epic ORDER BY created DESC", "summary,status,duedate")
    tasks_raw    = _search(f"project={_PROJECT_KEY} AND issuetype=Task ORDER BY created DESC", "summary,status,duedate")
    subtasks_raw = _search(f"project={_PROJECT_KEY} AND issuetype=Subtask ORDER BY created DESC", "summary,status,duedate,parent")
    return {
        "epics":    [_fmt_issue(i) for i in epics_raw],
        "tasks":    [_fmt_issue(i) for i in tasks_raw],
        "subtasks": [_fmt_issue(i, True) for i in subtasks_raw],
    }


def create_epic(title: str) -> dict | None:
    try:
        res = httpx.post(
            f"{_BASE}/rest/api/3/issue",
            auth=_AUTH,
            json={"fields": {"project": {"key": _PROJECT_KEY}, "summary": title, "issuetype": {"name": "Epic"}}},
            timeout=10,
        )
        res.raise_for_status()
        return {"key": res.json()["key"], "summary": title, "status": "To Do", "due_date": None}
    except Exception as e:
        logger.error("create_epic error: %s", e)
        return None


def create_task_item(title: str) -> dict | None:
    try:
        res = httpx.post(
            f"{_BASE}/rest/api/3/issue",
            auth=_AUTH,
            json={"fields": {"project": {"key": _PROJECT_KEY}, "summary": title, "issuetype": {"name": "Task"}}},
            timeout=10,
        )
        res.raise_for_status()
        key = res.json()["key"]
        if _BOARD_ID:
            _move_to_board(key)
        _transition_to_todo(key)
        return {"key": key, "summary": title, "status": "To Do", "due_date": None}
    except Exception as e:
        logger.error("create_task_item error: %s", e)
        return None


def create_subtask_item(title: str) -> dict | None:
    try:
        res = httpx.post(
            f"{_BASE}/rest/api/3/issue",
            auth=_AUTH,
            json={"fields": {
                "project":   {"key": _PROJECT_KEY},
                "parent":    {"key": _PARENT},
                "summary":   title,
                "issuetype": {"id": SUBTASK_TYPE_ID},
            }},
            timeout=10,
        )
        res.raise_for_status()
        return {"key": res.json()["key"], "summary": title, "status": "To Do", "due_date": None, "parent_key": _PARENT}
    except Exception as e:
        logger.error("create_subtask_item error: %s", e)
        return None


def update_summary(issue_key: str, summary: str) -> bool:
    try:
        res = httpx.put(
            f"{_BASE}/rest/api/3/issue/{issue_key}",
            auth=_AUTH,
            json={"fields": {"summary": summary}},
            timeout=10,
        )
        return res.status_code == 204
    except Exception as e:
        logger.error("update_summary error: %s", e)
        return False


def mark_done_issue(issue_key: str) -> bool:
    """Find the Done transition dynamically and apply it."""
    transitions = get_transitions(issue_key)
    done = next((t for t in transitions if "done" in t["name"].lower()), None)
    if not done:
        # fallback: hardcoded ID
        return apply_transition(issue_key, DONE_TRANSITION)
    return apply_transition(issue_key, done["id"])


def set_due_date(issue_key: str, due_date: str | None) -> bool:
    try:
        res = httpx.put(
            f"{_BASE}/rest/api/3/issue/{issue_key}",
            auth=_AUTH,
            json={"fields": {"duedate": due_date}},
            timeout=10,
        )
        return res.status_code == 204
    except Exception as e:
        logger.error("set_due_date error: %s", e)
        return False
