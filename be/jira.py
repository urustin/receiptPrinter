import os
import httpx

_BASE   = os.environ["JIRA_BASE_URL"].rstrip("/")   # https://flaresolution2.atlassian.net
_EMAIL  = os.environ["JIRA_EMAIL"]
_TOKEN  = os.environ["JIRA_API_TOKEN"]
_PARENT = os.environ["JIRA_PARENT_KEY"]             # SSW-63
_AUTH   = (_EMAIL, _TOKEN)

SUBTASK_TYPE_ID  = "10002"
DONE_TRANSITION  = "51"


def create_subtask(title: str) -> str | None:
    """Create a Jira subtask under JIRA_PARENT_KEY. Returns issue key or None."""
    try:
        res = httpx.post(
            f"{_BASE}/rest/api/3/issue",
            auth=_AUTH,
            json={
                "fields": {
                    "project":   {"key": _PARENT.split("-")[0]},
                    "parent":    {"key": _PARENT},
                    "summary":   title,
                    "issuetype": {"id": SUBTASK_TYPE_ID},
                }
            },
            timeout=10,
        )
        res.raise_for_status()
        return res.json()["key"]
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
