import os
import logging
import httpx
from dataclasses import dataclass

logger = logging.getLogger(__name__)

SUBTASK_TYPE_ID = "10002"
DONE_TRANSITION = "51"

# Only show issues assigned to the account whose token is configured (담당자 = 나).
# Applied to the printer board and the manage/arrange listings.
MINE = "assignee = currentUser()"

# Cache: "{base_url}|{project_key}" -> {"epic": ["에픽"], "task": ["작업"], "subtask": ["하위 작업"], "subtask_id": "id"}
_type_cache: dict = {}
_account_cache: dict = {}


def _discover_types(project_key: str, cfg: "JiraConfig") -> dict:
    """Discover issue type names by hierarchy level for a project.
    Returns dict with keys: epic (list), task (list), subtask (list), subtask_id (str).
    Falls back to English names if discovery fails.
    """
    cache_key = f"{cfg.base_url}|{project_key}"
    if cache_key in _type_cache:
        return _type_cache[cache_key]

    defaults = {
        "epic": ["Epic"], "task": ["Task"],
        "subtask": ["Subtask"], "subtask_id": SUBTASK_TYPE_ID,
    }
    try:
        res = httpx.get(
            f"{cfg.base_url}/rest/api/3/project/{project_key}",
            auth=cfg.auth, timeout=10,
        )
        res.raise_for_status()
        issue_types = res.json().get("issueTypes", [])
        result: dict = {"epic": [], "task": [], "subtask": [], "subtask_id": SUBTASK_TYPE_ID}
        for t in issue_types:
            hl = t.get("hierarchyLevel", 0)
            sub = t.get("subtask", False)
            if hl == 1 and not sub:
                result["epic"].append(t["name"])
            elif hl == -1 or sub:
                result["subtask"].append(t["name"])
                result["subtask_id"] = t["id"]  # last one wins; fine for single subtask type
            elif hl == 0 and not sub:
                result["task"].append(t["name"])
        # Fall back to English if any bucket is empty
        if not result["epic"]:   result["epic"]    = ["Epic"]
        if not result["task"]:   result["task"]    = ["Task"]
        if not result["subtask"]:result["subtask"] = ["Subtask"]
        _type_cache[cache_key] = result
        return result
    except Exception as e:
        logger.warning("_discover_types failed for %s: %s", project_key, e)
        return defaults


def _jql_in(names: list[str]) -> str:
    """Build JQL issuetype IN (...) clause from a list of names."""
    quoted = ", ".join(f'"{n}"' for n in names)
    return f"issuetype in ({quoted})"


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


def get_board_issue_keys(cfg: "JiraConfig | None" = None) -> set:
    """Keys currently visible on the configured Jira board, excluding backlog.

    For Jira simple boards the backlog endpoint can fail, so printer sync uses
    this board membership as the source of truth: on-board non-done issues are
    progress; assigned project issues outside this set are backlog.
    """
    c = _get_cfg(cfg)
    if not c.board_id:
        return set()
    keys: set = set()
    start = 0
    while True:
        try:
            res = httpx.get(
                f"{c.base_url}/rest/agile/1.0/board/{c.board_id}/issue",
                auth=c.auth,
                params={"maxResults": 100, "startAt": start, "fields": "status"},
                timeout=15,
            )
            res.raise_for_status()
            data = res.json()
            batch = data.get("issues", [])
            keys.update(i["key"] for i in batch)
            if start + len(batch) >= data.get("total", 0) or not batch:
                break
            start += len(batch)
        except Exception as e:
            logger.error("get_board_issue_keys error: %s", e)
            break
    return keys


def get_backlog_keys(cfg: "JiraConfig | None" = None) -> set:
    """Keys of issues currently in the board's backlog.
    The printer board mirrors the Jira board and excludes these.
    Only Kanban backlog is excluded. In Scrum/simple boards, backlog is a valid
    planning area and excluding it would remove active local printer jobs during
    sync.
    Note: newly created issues already land on the board (not the backlog);
    POSTing to /board/{id}/issue would instead move them INTO the backlog,
    which is why the create flow no longer does that.
    """
    c = _get_cfg(cfg)
    if not c.board_id:
        return set()
    keys: set = set()
    start = 0
    while True:
        try:
            board = httpx.get(
                f"{c.base_url}/rest/agile/1.0/board/{c.board_id}",
                auth=c.auth,
                timeout=10,
            )
            board.raise_for_status()
            if board.json().get("type") != "kanban":
                return set()

            res = httpx.get(
                f"{c.base_url}/rest/agile/1.0/board/{c.board_id}/backlog",
                auth=c.auth,
                params={"maxResults": 100, "startAt": start, "fields": "status"},
                timeout=15,
            )
            res.raise_for_status()
            data = res.json()
            batch = data.get("issues", [])
            keys.update(i["key"] for i in batch)
            if start + len(batch) >= data.get("total", 0) or not batch:
                break
            start += len(batch)
        except Exception as e:
            logger.error("get_backlog_keys error: %s", e)
            break
    return keys


def _move_to_board(issue_key: str, cfg: JiraConfig) -> bool:
    """Move a project-level issue from backlog onto the configured board."""
    if not cfg.board_id:
        return True
    try:
        res = httpx.post(
            f"{cfg.base_url}/rest/agile/1.0/board/{cfg.board_id}/issue",
            auth=cfg.auth,
            json={"issues": [issue_key]},
            timeout=10,
        )
        if res.status_code == 204:
            return True
        logger.error("_move_to_board failed: issue=%s board=%s status=%s body=%s",
                     issue_key, cfg.board_id, res.status_code, res.text[:200])
        return False
    except Exception as e:
        logger.error("_move_to_board error: %s", e)
        return False


def _account_ids_for_email(email: str, cfg: JiraConfig) -> set[str]:
    if not email:
        return set()
    cache_key = f"{cfg.base_url}|email|{email.lower()}"
    if cache_key in _account_cache:
        cached = _account_cache[cache_key]
        return cached if isinstance(cached, set) else {cached}
    try:
        res = httpx.get(
            f"{cfg.base_url}/rest/api/3/user/search",
            auth=cfg.auth,
            params={"query": email, "maxResults": 10},
            timeout=10,
        )
        res.raise_for_status()
        ids = {
            u.get("accountId")
            for u in res.json()
            if (u.get("emailAddress") or "").lower() == email.lower() and u.get("accountId")
        }
        if not ids:
            ids = {u.get("accountId") for u in res.json() if u.get("accountId")}
        ids.discard(None)
        _account_cache[cache_key] = ids
        return ids
    except Exception as e:
        logger.error("_account_ids_for_email error: %s", e)
        return set()


def _assignee_matches(issue: dict, assignee_email: "str | None", account_ids: set[str]) -> bool:
    if not assignee_email:
        return True
    assignee = issue.get("fields", {}).get("assignee")
    if not assignee:
        return False
    if assignee.get("accountId") in account_ids:
        return True
    return (assignee.get("emailAddress") or "").lower() == assignee_email.lower()


def _current_account_id(cfg: JiraConfig) -> "str | None":
    cache_key = f"{cfg.base_url}|{cfg.email}"
    if cache_key in _account_cache:
        return _account_cache[cache_key]
    try:
        res = httpx.get(
            f"{cfg.base_url}/rest/api/3/myself",
            auth=cfg.auth,
            timeout=10,
        )
        res.raise_for_status()
        account_id = res.json().get("accountId")
        if account_id:
            _account_cache[cache_key] = account_id
        return account_id
    except Exception as e:
        logger.error("_current_account_id error: %s", e)
        return None


def _assign_to_email(issue_key: str, email: str, cfg: JiraConfig) -> bool:
    account_ids = _account_ids_for_email(email, cfg)
    account_id = next(iter(account_ids), None)
    if not account_id:
        return False
    try:
        res = httpx.put(
            f"{cfg.base_url}/rest/api/3/issue/{issue_key}/assignee",
            auth=cfg.auth,
            json={"accountId": account_id},
            timeout=10,
        )
        if res.status_code == 204:
            return True
        logger.error("_assign_to_email failed: issue=%s email=%s status=%s body=%s",
                     issue_key, email, res.status_code, res.text[:200])
        return False
    except Exception as e:
        logger.error("_assign_to_email error: %s", e)
        return False


def _assign_to_me(issue_key: str, cfg: JiraConfig) -> bool:
    account_id = _current_account_id(cfg)
    if not account_id:
        return False
    try:
        res = httpx.put(
            f"{cfg.base_url}/rest/api/3/issue/{issue_key}/assignee",
            auth=cfg.auth,
            json={"accountId": account_id},
            timeout=10,
        )
        if res.status_code == 204:
            return True
        logger.error("_assign_to_me failed: issue=%s status=%s body=%s",
                     issue_key, res.status_code, res.text[:200])
        return False
    except Exception as e:
        logger.error("_assign_to_me error: %s", e)
        return False


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
            (t for t in transitions
             if t["to"].get("statusCategory", {}).get("key") == "new"),
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


def create_issue(title: str, cfg: "JiraConfig | None" = None, add_to_board: bool = True, assignee_email: "str | None" = None) -> "str | None":
    c = _get_cfg(cfg)
    try:
        types = _discover_types(c.project_key, c)
        if c.ticket_mode == "TASK":
            fields = {
                "project":   {"key": c.project_key},
                "summary":   title,
                "issuetype": {"name": types["task"][0]},
            }
        else:
            issue_type: dict = {"id": types["subtask_id"]} if types.get("subtask_id") else {"name": types["subtask"][0]}
            fields = {
                "project":   {"key": c.project_key},
                "parent":    {"key": c.parent_key},
                "summary":   title,
                "issuetype": issue_type,
            }

        res = httpx.post(
            f"{c.base_url}/rest/api/3/issue",
            auth=c.auth,
            json={"fields": fields},
            timeout=10,
        )
        res.raise_for_status()
        issue_key = res.json()["key"]
        if not (assignee_email and _assign_to_email(issue_key, assignee_email, c)):
            _assign_to_me(issue_key, c)

        if c.ticket_mode == "TASK" and add_to_board:
            # Ensure new task is visible on the board, not hidden in backlog.
            _transition_to_todo(issue_key, c)
            _move_to_board(issue_key, c)

        return issue_key
    except Exception as e:
        logger.error("create_issue error: %s", e)
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
    return mark_done_issue(issue_key, cfg)


def _search(jql: str, fields: str, cfg: JiraConfig, max_results: int = 100) -> list:
    # The /search/jql endpoint paginates via nextPageToken (not startAt/total).
    issues = []
    next_token = None
    while True:
        try:
            params = {"jql": jql, "fields": fields, "maxResults": max_results}
            if next_token:
                params["nextPageToken"] = next_token
            res = httpx.get(
                f"{cfg.base_url}/rest/api/3/search/jql",
                auth=cfg.auth,
                params=params,
                timeout=15,
            )
            res.raise_for_status()
            data = res.json()
            batch = data.get("issues", [])
            issues.extend(batch)
            next_token = data.get("nextPageToken")
            if not next_token or not batch:
                break
        except Exception as e:
            logger.error("_search error jql=%s: %s", jql, e)
            break
    return issues


def get_epics_and_tasks(cfg: "JiraConfig | None" = None) -> dict:
    c = _get_cfg(cfg)
    types = _discover_types(c.project_key, c)
    epics_raw = _search(
        f"project={c.project_key} AND {_jql_in(types['epic'])} AND {MINE} ORDER BY created DESC",
        "summary,status", c,
    )
    epics = [
        {
            "key": i["key"],
            "summary": i["fields"]["summary"],
            "status": i["fields"]["status"]["name"],
            "status_category": i["fields"]["status"].get("statusCategory", {}).get("key", ""),
        }
        for i in epics_raw
    ]

    epic_keys = {e["key"] for e in epics}

    tasks_raw = _search(
        f"project={c.project_key} AND {_jql_in(types['task'])} AND {MINE} ORDER BY created DESC",
        "summary,status,parent,customfield_10014", c,
    )
    tasks = []
    for i in tasks_raw:
        f = i["fields"]
        epic_key = None
        # Check parent field — language-agnostic: just check if parent key is a known epic
        parent = f.get("parent")
        if parent and parent.get("key") in epic_keys:
            epic_key = parent["key"]
        # Fallback: classic epic link custom field
        if epic_key is None:
            cf = f.get("customfield_10014")
            if cf:
                epic_key = cf
        tasks.append({
            "key": i["key"],
            "summary": f["summary"],
            "status": f["status"]["name"],
            "status_category": f["status"].get("statusCategory", {}).get("key", ""),
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
    types = _discover_types(c.project_key, c)
    tasks_raw = _search(
        f"project={c.project_key} AND {_jql_in(types['task'])} AND {MINE} ORDER BY created DESC",
        "summary,status", c,
    )
    tasks = [
        {
            "key": i["key"],
            "summary": i["fields"]["summary"],
            "status": i["fields"]["status"]["name"],
            "status_category": i["fields"]["status"].get("statusCategory", {}).get("key", ""),
        }
        for i in tasks_raw
    ]

    subtasks_raw = _search(
        f"project={c.project_key} AND issuetype in subTaskIssueTypes() AND {MINE} ORDER BY created DESC",
        "summary,status,parent", c,
    )
    subtasks = []
    for i in subtasks_raw:
        parent = i["fields"].get("parent")
        subtasks.append({
            "key": i["key"],
            "summary": i["fields"]["summary"],
            "status": i["fields"]["status"]["name"],
            "status_category": i["fields"]["status"].get("statusCategory", {}).get("key", ""),
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
            {
                "id":                  t["id"],
                "name":                t["to"]["name"],
                "status_category_key": t["to"].get("statusCategory", {}).get("key", ""),
            }
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
    types = _discover_types(c.project_key, c)
    epics_raw    = _search(f"project={c.project_key} AND {_jql_in(types['epic'])} AND {MINE} ORDER BY created DESC",    "summary,status,duedate", c)
    tasks_raw    = _search(f"project={c.project_key} AND {_jql_in(types['task'])} AND {MINE} ORDER BY created DESC",    "summary,status,duedate", c)
    subtasks_raw = _search(f"project={c.project_key} AND issuetype in subTaskIssueTypes() AND {MINE} ORDER BY created DESC", "summary,status,duedate,parent", c)
    return {
        "epics":    [_fmt_issue(i) for i in epics_raw],
        "tasks":    [_fmt_issue(i) for i in tasks_raw],
        "subtasks": [_fmt_issue(i, True) for i in subtasks_raw],
    }


def create_epic(title: str, cfg: "JiraConfig | None" = None) -> "dict | None":
    c = _get_cfg(cfg)
    try:
        types = _discover_types(c.project_key, c)
        res = httpx.post(
            f"{c.base_url}/rest/api/3/issue",
            auth=c.auth,
            json={"fields": {"project": {"key": c.project_key}, "summary": title, "issuetype": {"name": types["epic"][0]}}},
            timeout=10,
        )
        res.raise_for_status()
        key = res.json()["key"]
        _assign_to_me(key, c)
        return {"key": key, "summary": title, "status": "To Do", "status_done": False, "due_date": None}
    except Exception as e:
        logger.error("create_epic error: %s", e)
        return None


def create_task_item(title: str, cfg: "JiraConfig | None" = None) -> "dict | None":
    c = _get_cfg(cfg)
    try:
        types = _discover_types(c.project_key, c)
        res = httpx.post(
            f"{c.base_url}/rest/api/3/issue",
            auth=c.auth,
            json={"fields": {"project": {"key": c.project_key}, "summary": title, "issuetype": {"name": types["task"][0]}}},
            timeout=10,
        )
        res.raise_for_status()
        key = res.json()["key"]
        _assign_to_me(key, c)
        # Ensure new task is visible on the board, not hidden in backlog.
        _transition_to_todo(key, c)
        _move_to_board(key, c)
        return {"key": key, "summary": title, "status": "To Do", "status_done": False, "due_date": None}
    except Exception as e:
        logger.error("create_task_item error: %s", e)
        return None


def create_subtask_item(title: str, cfg: "JiraConfig | None" = None) -> "dict | None":
    c = _get_cfg(cfg)
    try:
        types = _discover_types(c.project_key, c)
        issue_type: dict = {"id": types["subtask_id"]} if types.get("subtask_id") else {"name": types["subtask"][0]}
        res = httpx.post(
            f"{c.base_url}/rest/api/3/issue",
            auth=c.auth,
            json={"fields": {
                "project":   {"key": c.project_key},
                "parent":    {"key": c.parent_key},
                "summary":   title,
                "issuetype": issue_type,
            }},
            timeout=10,
        )
        res.raise_for_status()
        key = res.json()["key"]
        _assign_to_me(key, c)
        return {"key": key, "summary": title, "status": "To Do", "status_done": False, "due_date": None, "parent_key": c.parent_key}
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
    done = next((t for t in transitions if t["status_category_key"] == "done"), None)
    if not done:
        return False
    return apply_transition(issue_key, done["id"], cfg)


def get_printer_items(cfg: "JiraConfig | None" = None, assignee_email: "str | None" = None) -> list:
    """Return items from Jira that belong on the printer page.
    SUBTASK mode: subtasks under parent_key.
    TASK mode: tasks in the project.
    Each item has: key, summary, status_done (bool), in_backlog (bool).
    If assignee_email is set, only issues assigned to that logged-in user match.
    """
    c = _get_cfg(cfg)
    if c.ticket_mode == "SUBTASK":
        jql = f"project={c.project_key} AND issuetype in subTaskIssueTypes() ORDER BY created DESC"
    else:
        types = _discover_types(c.project_key, c)
        jql = f"project={c.project_key} AND {_jql_in(types['task'])} ORDER BY created DESC"
    raw = _search(jql, "summary,status,issuetype,assignee", c)
    board_keys = get_board_issue_keys(c)
    account_ids = _account_ids_for_email(assignee_email, c) if assignee_email else set()
    items = []
    for i in raw:
        if not _assignee_matches(i, assignee_email, account_ids):
            continue
        status_category = i["fields"]["status"].get("statusCategory", {}).get("key", "")
        status_done = status_category == "done"
        items.append({
            "key": i["key"],
            "summary": i["fields"]["summary"],
            "status_done": status_done,
            "status_category": status_category,
            "in_backlog": status_category == "new" or bool(c.board_id and not status_done and i["key"] not in board_keys),
            "type": "subtask" if i["fields"]["issuetype"].get("subtask") else "task",
        })
    return items


def mark_in_progress(issue_key: str, cfg: "JiraConfig | None" = None) -> bool:
    c = _get_cfg(cfg)
    _move_to_board(issue_key, c)
    transitions = get_transitions(issue_key, c)
    inprog = next((t for t in transitions if t["status_category_key"] == "indeterminate"), None)
    if not inprog:
        return False
    return apply_transition(issue_key, inprog["id"], c)


def mark_backlog(issue_key: str, cfg: "JiraConfig | None" = None) -> bool:
    transitions = get_transitions(issue_key, cfg)
    todo = next((t for t in transitions if t["status_category_key"] == "new"), None)
    if not todo:
        return False
    return apply_transition(issue_key, todo["id"], cfg)


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
