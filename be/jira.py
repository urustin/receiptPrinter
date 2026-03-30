import os
import logging
import httpx
from dataclasses import dataclass

logger = logging.getLogger(__name__)

SUBTASK_TYPE_ID = "10002"
DONE_TRANSITION = "51"


@dataclass
class JiraConfig:
    base_url: str
    email: str
    api_token: str
    project_key: str
    parent_key: str = ""
    board_id: str = ""
    temp_key: str = ""
    ticket_mode: str = "SUBTASK"

    @property
    def auth(self):
        return (self.email, self.api_token)


def _env_cfg() -> "JiraConfig | None":
    """Build config from environment variables. Returns None if not configured."""
    base  = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
    email = os.environ.get("JIRA_EMAIL", "")
    token = os.environ.get("JIRA_API_TOKEN", "")
    if not (base and email and token):
        return None
    parent  = os.environ.get("JIRA_PARENT_KEY", "")
    project = os.environ.get("JIRA_PROJECT_KEY") or (parent.split("-")[0] if parent else "")
    return JiraConfig(
        base_url=base,
        email=email,
        api_token=token,
        project_key=project,
        parent_key=parent,
        board_id=os.environ.get("JIRA_BOARD_ID", ""),
        temp_key=os.environ.get("JIRA_TEMP_KEY", ""),
        ticket_mode=os.environ.get("TICKET", "SUBTASK").upper(),
    )


def _get_cfg(cfg: "JiraConfig | None") -> JiraConfig:
    if cfg is not None:
        return cfg
    env = _env_cfg()
    if env is not None:
        return env
    raise ValueError("Jira not configured")


def _move_to_board(issue_key: str, cfg: JiraConfig) -> None:
    if not cfg.board_id:
        return
    try:
        httpx.post(
            f"{cfg.base_url}/rest/agile/1.0/board/{cfg.board_id}/issue",
            auth=cfg.auth,
            json={"issues": [issue_key]},
            timeout=10,
        )
    except Exception as e:
        logger.error("_move_to_board error: %s", e)


def _transition_to_todo(issue_key: str, cfg: JiraConfig) -> None:
    try:
        res = httpx.get(
            f"{cfg.base_url}/rest/api/3/issue/{issue_key}/transitions",
            auth=cfg.auth,
            timeout=10,
        )
        res.raise_for_status()
        transitions = res.json().get("transitions", [])
        todo = next(
            (t for t in transitions if t["to"]["name"].lower() == "to do"),
            None,
        )
        if todo:
            httpx.post(
                f"{cfg.base_url}/rest/api/3/issue/{issue_key}/transitions",
                auth=cfg.auth,
                json={"transition": {"id": todo["id"]}},
                timeout=10,
            )
    except Exception as e:
        logger.error("_transition_to_todo error: %s", e)


def create_issue(title: str, cfg: "JiraConfig | None" = None) -> "str | None":
    c = _get_cfg(cfg)
    try:
        if c.ticket_mode == "TASK":
            fields = {
                "project":   {"key": c.project_key},
                "summary":   title,
                "issuetype": {"name": "Task"},
            }
        else:
            fields = {
                "project":   {"key": c.project_key},
                "parent":    {"key": c.parent_key},
                "summary":   title,
                "issuetype": {"id": SUBTASK_TYPE_ID},
            }

        res = httpx.post(
            f"{c.base_url}/rest/api/3/issue",
            auth=c.auth,
            json={"fields": fields},
            timeout=10,
        )
        res.raise_for_status()
        issue_key = res.json()["key"]

        if c.ticket_mode == "TASK":
            _move_to_board(issue_key, c)
            _transition_to_todo(issue_key, c)

        return issue_key
    except Exception:
        return None


def delete_issue(issue_key: str, cfg: "JiraConfig | None" = None) -> bool:
    c = _get_cfg(cfg)
    try:
        res = httpx.delete(
            f"{c.base_url}/rest/api/3/issue/{issue_key}",
            auth=c.auth,
            timeout=10,
        )
        return res.status_code == 204
    except Exception:
        return False


def mark_done(issue_key: str, cfg: "JiraConfig | None" = None) -> bool:
    c = _get_cfg(cfg)
    try:
        res = httpx.post(
            f"{c.base_url}/rest/api/3/issue/{issue_key}/transitions",
            auth=c.auth,
            json={"transition": {"id": DONE_TRANSITION}},
            timeout=10,
        )
        return res.status_code == 204
    except Exception:
        return False


def _search(jql: str, fields: str, cfg: JiraConfig, max_results: int = 100) -> list:
    issues = []
    start = 0
    while True:
        try:
            res = httpx.get(
                f"{cfg.base_url}/rest/api/3/search/jql",
                auth=cfg.auth,
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


def get_epics_and_tasks(cfg: "JiraConfig | None" = None) -> dict:
    c = _get_cfg(cfg)
    epics_raw = _search(
        f"project={c.project_key} AND issuetype=Epic ORDER BY created DESC",
        "summary,status", c,
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
        f"project={c.project_key} AND issuetype=Task ORDER BY created DESC",
        "summary,status,parent,customfield_10014", c,
    )
    tasks = []
    for i in tasks_raw:
        f = i["fields"]
        epic_key = None
        parent = f.get("parent")
        if parent:
            ptype = parent.get("fields", {}).get("issuetype", {}).get("name", "")
            if ptype == "Epic":
                epic_key = parent["key"]
        if epic_key is None:
            cf = f.get("customfield_10014")
            if cf:
                epic_key = cf
        tasks.append({
            "key": i["key"],
            "summary": f["summary"],
            "status": f["status"]["name"],
            "epic_key": epic_key,
        })

    return {"epics": epics, "tasks": tasks}


def assign_task_to_epic(task_key: str, epic_key: "str | None", cfg: "JiraConfig | None" = None) -> bool:
    c = _get_cfg(cfg)
    try:
        payload = {"fields": {"parent": {"key": epic_key}}} if epic_key else {"fields": {"parent": None}}
        res = httpx.put(f"{c.base_url}/rest/api/3/issue/{task_key}", auth=c.auth, json=payload, timeout=10)
        if res.status_code == 204:
            return True
        payload2 = {"fields": {"customfield_10014": epic_key}}
        res2 = httpx.put(f"{c.base_url}/rest/api/3/issue/{task_key}", auth=c.auth, json=payload2, timeout=10)
        if res2.status_code == 204:
            return True
        logger.error("assign_task_to_epic failed: task=%s epic=%s status=%s body=%s",
                     task_key, epic_key, res.status_code, res.text[:200])
        return False
    except Exception as e:
        logger.error("assign_task_to_epic error: %s", e)
        return False


def get_tasks_and_subtasks(cfg: "JiraConfig | None" = None) -> dict:
    c = _get_cfg(cfg)
    tasks_raw = _search(
        f"project={c.project_key} AND issuetype=Task ORDER BY created DESC",
        "summary,status", c,
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
        f"project={c.project_key} AND issuetype=Subtask ORDER BY created DESC",
        "summary,status,parent", c,
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

    temp_key = c.temp_key
    if not temp_key:
        temp_task = next((t for t in tasks if t["summary"].strip().upper() == "TEMP"), None)
        temp_key = temp_task["key"] if temp_task else c.parent_key

    return {"tasks": tasks, "subtasks": subtasks, "temp_key": temp_key}


def get_transitions(issue_key: str, cfg: "JiraConfig | None" = None) -> list:
    c = _get_cfg(cfg)
    try:
        res = httpx.get(
            f"{c.base_url}/rest/api/3/issue/{issue_key}/transitions",
            auth=c.auth,
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


def apply_transition(issue_key: str, transition_id: str, cfg: "JiraConfig | None" = None) -> bool:
    c = _get_cfg(cfg)
    try:
        res = httpx.post(
            f"{c.base_url}/rest/api/3/issue/{issue_key}/transitions",
            auth=c.auth,
            json={"transition": {"id": transition_id}},
            timeout=10,
        )
        return res.status_code == 204
    except Exception as e:
        logger.error("apply_transition error: %s", e)
        return False


def assign_subtask_to_task(subtask_key: str, task_key: str, cfg: "JiraConfig | None" = None) -> bool:
    c = _get_cfg(cfg)
    try:
        res = httpx.put(
            f"{c.base_url}/rest/api/3/issue/{subtask_key}",
            auth=c.auth,
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


def get_all_items(cfg: "JiraConfig | None" = None) -> dict:
    c = _get_cfg(cfg)
    epics_raw    = _search(f"project={c.project_key} AND issuetype=Epic ORDER BY created DESC",    "summary,status,duedate", c)
    tasks_raw    = _search(f"project={c.project_key} AND issuetype=Task ORDER BY created DESC",    "summary,status,duedate", c)
    subtasks_raw = _search(f"project={c.project_key} AND issuetype=Subtask ORDER BY created DESC", "summary,status,duedate,parent", c)
    return {
        "epics":    [_fmt_issue(i) for i in epics_raw],
        "tasks":    [_fmt_issue(i) for i in tasks_raw],
        "subtasks": [_fmt_issue(i, True) for i in subtasks_raw],
    }


def create_epic(title: str, cfg: "JiraConfig | None" = None) -> "dict | None":
    c = _get_cfg(cfg)
    try:
        res = httpx.post(
            f"{c.base_url}/rest/api/3/issue",
            auth=c.auth,
            json={"fields": {"project": {"key": c.project_key}, "summary": title, "issuetype": {"name": "Epic"}}},
            timeout=10,
        )
        res.raise_for_status()
        return {"key": res.json()["key"], "summary": title, "status": "To Do", "status_done": False, "due_date": None}
    except Exception as e:
        logger.error("create_epic error: %s", e)
        return None


def create_task_item(title: str, cfg: "JiraConfig | None" = None) -> "dict | None":
    c = _get_cfg(cfg)
    try:
        res = httpx.post(
            f"{c.base_url}/rest/api/3/issue",
            auth=c.auth,
            json={"fields": {"project": {"key": c.project_key}, "summary": title, "issuetype": {"name": "Task"}}},
            timeout=10,
        )
        res.raise_for_status()
        key = res.json()["key"]
        if c.board_id:
            _move_to_board(key, c)
        _transition_to_todo(key, c)
        return {"key": key, "summary": title, "status": "To Do", "status_done": False, "due_date": None}
    except Exception as e:
        logger.error("create_task_item error: %s", e)
        return None


def create_subtask_item(title: str, cfg: "JiraConfig | None" = None) -> "dict | None":
    c = _get_cfg(cfg)
    try:
        res = httpx.post(
            f"{c.base_url}/rest/api/3/issue",
            auth=c.auth,
            json={"fields": {
                "project":   {"key": c.project_key},
                "parent":    {"key": c.parent_key},
                "summary":   title,
                "issuetype": {"id": SUBTASK_TYPE_ID},
            }},
            timeout=10,
        )
        res.raise_for_status()
        return {"key": res.json()["key"], "summary": title, "status": "To Do", "status_done": False, "due_date": None, "parent_key": c.parent_key}
    except Exception as e:
        logger.error("create_subtask_item error: %s", e)
        return None


def update_summary(issue_key: str, summary: str, cfg: "JiraConfig | None" = None) -> bool:
    c = _get_cfg(cfg)
    try:
        res = httpx.put(
            f"{c.base_url}/rest/api/3/issue/{issue_key}",
            auth=c.auth,
            json={"fields": {"summary": summary}},
            timeout=10,
        )
        return res.status_code == 204
    except Exception as e:
        logger.error("update_summary error: %s", e)
        return False


def mark_done_issue(issue_key: str, cfg: "JiraConfig | None" = None) -> bool:
    transitions = get_transitions(issue_key, cfg)
    done = next((t for t in transitions if "done" in t["name"].lower()), None)
    if not done:
        return apply_transition(issue_key, DONE_TRANSITION, cfg)
    return apply_transition(issue_key, done["id"], cfg)


def set_due_date(issue_key: str, due_date: "str | None", cfg: "JiraConfig | None" = None) -> bool:
    c = _get_cfg(cfg)
    try:
        res = httpx.put(
            f"{c.base_url}/rest/api/3/issue/{issue_key}",
            auth=c.auth,
            json={"fields": {"duedate": due_date}},
            timeout=10,
        )
        return res.status_code == 204
    except Exception as e:
        logger.error("set_due_date error: %s", e)
        return False
