"""Odoo desktop client starter package."""

from dataclasses import dataclass
import datetime
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OdooConnectionError(Exception):
    """Raised when an Odoo server cannot be reached or authentication fails."""


@dataclass(frozen=True)
class LoginResult:
    uid: int
    database: str
    base_url: str = ""


@dataclass(frozen=True)
class OdooSession:
    base_url: str
    database: str
    uid: int
    api_key: str


@dataclass(frozen=True)
class TaskInfo:
    id: int
    name: str
    project_id: int
    project: str
    stage: str
    deadline: str = ""  # local date, YYYY-MM-DD, or ""
    updated: str = ""  # local date of the last change in Odoo
    created: str = ""
    starred: bool = False
    state: str = ""
    done: bool = False  # closed in Odoo, or sitting in a folded / "Done"-like stage


@dataclass(frozen=True)
class SubTask:
    id: int
    name: str
    stage: str
    closed: bool = False


@dataclass(frozen=True)
class TaskMessage:
    author: str
    date: str  # local 'YYYY-MM-DD HH:MM'
    body: str  # HTML


@dataclass(frozen=True)
class TaskDetail:
    id: int
    name: str
    project: str = ""
    stage: str = ""
    state: str = ""
    deadline: str = ""
    starred: bool = False
    done: bool = False
    description: str = ""  # HTML
    assignees: tuple = ()
    tags: tuple = ()
    customer: str = ""
    milestone: str = ""
    parent: str = ""
    created: str = ""
    updated: str = ""
    allocated_hours: float = 0.0
    spent_hours: float = 0.0
    subtasks: tuple = ()
    messages: tuple = ()


CLOSED_STATES = ("1_done", "1_canceled")
# Stage names that mean "finished" even when the stage is not folded in Odoo.
DONE_STAGE_WORDS = ("done", "cancel", "closed", "termin", "annul", "selesai", "batal")
STATE_LABELS = {
    "01_in_progress": "In progress",
    "02_changes_requested": "Changes requested",
    "03_approved": "Approved",
    "04_waiting_normal": "Waiting",
    "1_done": "Done",
    "1_canceled": "Cancelled",
}
TASK_FIELDS = (
    "name",
    "project_id",
    "stage_id",
    "date_deadline",
    "priority",
    "write_date",
    "create_date",
    "state",
)
DETAIL_FIELDS = TASK_FIELDS + (
    "description",
    "user_ids",
    "tag_ids",
    "partner_id",
    "milestone_id",
    "parent_id",
    "child_ids",
    "allocated_hours",
    "effective_hours",
)


def to_local_datetime(value):
    """UTC 'YYYY-MM-DD HH:MM:SS' from Odoo -> local 'YYYY-MM-DD HH:MM'."""
    if not value or not isinstance(value, str):
        return ""
    try:
        moment = datetime.datetime.fromisoformat(value[:19]).replace(tzinfo=datetime.timezone.utc)
        return moment.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ""


def _name(pair):
    return pair[1] if isinstance(pair, (list, tuple)) and len(pair) > 1 else ""


def to_local_date(value):
    """Odoo sends dates as 'YYYY-MM-DD' or UTC 'YYYY-MM-DD HH:MM:SS'."""
    if not value or not isinstance(value, str):
        return ""
    try:
        if len(value) <= 10:
            return datetime.date.fromisoformat(value).isoformat()
        moment = datetime.datetime.fromisoformat(value[:19]).replace(tzinfo=datetime.timezone.utc)
        return moment.astimezone().date().isoformat()
    except ValueError:
        return ""


class OdooClient:
    def __init__(self, timeout=20):
        self.timeout = timeout
        self._field_cache = {}
        self._stage_fold_cache = {}

    @staticmethod
    def normalize_url(url):
        value = url.strip().rstrip("/")
        if not value:
            raise OdooConnectionError("Enter your Odoo server URL.")
        if "://" not in value:
            value = "https://" + value
        if not value.startswith(("https://", "http://")):
            raise OdooConnectionError("The server URL must use HTTP or HTTPS.")
        return value

    def login(self, server_url, email, api_key, database=""):
        base_url = self.normalize_url(server_url)
        email = email.strip()
        database = database.strip()
        if not email:
            raise OdooConnectionError("Enter your Odoo email address.")
        if not api_key:
            raise OdooConnectionError("Enter your Odoo password or API key.")

        if not database:
            try:
                databases = self._json_rpc(base_url + "/web/database/list", {})
            except OdooConnectionError as exc:
                if "unreadable response" in str(exc).lower():
                    raise OdooConnectionError(
                        "Could not read Odoo's database list. Enter the database name manually."
                    ) from exc
                raise
            if not isinstance(databases, list) or not databases:
                raise OdooConnectionError(
                    "Odoo did not provide a database list. Enter the database name manually."
                )
            if len(databases) != 1:
                raise OdooConnectionError(
                    "Odoo has multiple databases; choose a database and try again."
                )
            database = str(databases[0])

        uid = self._json_rpc(
            base_url + "/jsonrpc",
            {
                "service": "common",
                "method": "authenticate",
                "args": [database, email, api_key, {}],
            },
        )
        if not isinstance(uid, int) or isinstance(uid, bool) or uid <= 0:
            raise OdooConnectionError("Login failed. Check your email and password. If your account uses two-step verification, use an API key instead.")
        return LoginResult(uid=uid, database=database, base_url=base_url)

    def call_model(self, session, model, method, args, kwargs=None):
        return self._json_rpc(
            session.base_url + "/jsonrpc",
            {
                "service": "object",
                "method": "execute_kw",
                "args": [
                    session.database,
                    session.uid,
                    session.api_key,
                    model,
                    method,
                    args,
                    kwargs or {},
                ],
            },
        )

    def task_fields(self, session):
        """Names of the project.task fields this server has (differs by Odoo version)."""
        key = (session.base_url, session.database)
        if key not in self._field_cache:
            fields = self.call_model(session, "project.task", "fields_get", [], {"attributes": ["type"]})
            self._field_cache[key] = set(fields or {})
        return self._field_cache[key]

    def folded_stages(self, session, stage_ids):
        """Which of these stages are folded in the kanban (Odoo's way of saying 'finished')."""
        key = (session.base_url, session.database)
        cache = self._stage_fold_cache.setdefault(key, {})
        missing = sorted({i for i in stage_ids if i and i not in cache})
        if missing:
            try:
                records = self.call_model(session, "project.task.type", "read", [missing, ["fold"]])
            except OdooConnectionError:
                records = []
            for record in records or []:
                cache[record["id"]] = bool(record.get("fold"))
        return {i for i in stage_ids if cache.get(i)}

    @staticmethod
    def is_done(state, stage, folded):
        """`stage` is Odoo's [id, name] pair (or False)."""
        if state in CLOSED_STATES:
            return True
        if not stage:
            return False
        name = str(stage[1]).lower()
        return stage[0] in folded or any(word in name for word in DONE_STAGE_WORDS)

    def fetch_my_tasks(self, session):
        """Tasks assigned to the signed-in user, across all projects.

        Tasks closed in Odoo are left out. Tasks that only sit in a finished
        stage come back with `done=True` so the app can hide them by default.
        """
        available = self.task_fields(session)
        domain = [["user_ids", "in", [session.uid]]]
        if "state" in available:
            domain.append(["state", "not in", list(CLOSED_STATES)])
        elif "is_closed" in available:
            domain.append(["is_closed", "=", False])
        wanted = [name for name in TASK_FIELDS if name in available]
        records = self.call_model(
            session,
            "project.task",
            "search_read",
            [domain, wanted],
            {"order": "id desc", "limit": 2000},
        )
        records = records or []
        folded = self.folded_stages(session, [r["stage_id"][0] for r in records if r.get("stage_id")])
        tasks = []
        for record in records or []:
            project = record.get("project_id")
            stage = record.get("stage_id")
            tasks.append(
                TaskInfo(
                    id=record.get("id", 0),
                    name=record.get("name") or "Untitled task",
                    project_id=project[0] if project else 0,
                    project=project[1] if project else "Private tasks",
                    stage=stage[1] if stage else "No stage",
                    deadline=to_local_date(record.get("date_deadline")),
                    updated=to_local_date(record.get("write_date")),
                    created=to_local_date(record.get("create_date")),
                    starred=str(record.get("priority") or "0") not in ("0", ""),
                    state=record.get("state") or "",
                    done=self.is_done(record.get("state") or "", stage, folded),
                )
            )
        return tasks

    def _names(self, session, model, ids):
        """Display names for related records; empty if this account may not read them."""
        if not ids:
            return ()
        try:
            records = self.call_model(session, model, "read", [list(ids), ["display_name"]])
        except OdooConnectionError:
            return ()
        by_id = {record["id"]: record.get("display_name") or "" for record in records or []}
        return tuple(by_id[i] for i in ids if by_id.get(i))

    def fetch_task_detail(self, session, task_id):
        """Everything shown on the in-app task page. Read-only."""
        available = self.task_fields(session)
        wanted = [name for name in DETAIL_FIELDS if name in available]
        records = self.call_model(session, "project.task", "read", [[int(task_id)], wanted])
        if not records:
            raise OdooConnectionError("This task no longer exists or you can no longer see it.")
        record = records[0]
        stage_pair = record.get("stage_id")
        folded = self.folded_stages(session, [stage_pair[0]] if stage_pair else [])

        subtasks = []
        child_ids = record.get("child_ids") or []
        if child_ids:
            fields = ["name", "stage_id"] + (["state"] if "state" in available else [])
            try:
                children = self.call_model(session, "project.task", "read", [child_ids, fields])
            except OdooConnectionError:
                children = []
            for child in children or []:
                subtasks.append(
                    SubTask(
                        id=child.get("id", 0),
                        name=child.get("name") or "Untitled task",
                        stage=_name(child.get("stage_id")) or "No stage",
                        closed=(child.get("state") or "") in CLOSED_STATES,
                    )
                )

        messages = []
        try:
            raw = self.call_model(
                session,
                "mail.message",
                "search_read",
                [
                    [
                        ["model", "=", "project.task"],
                        ["res_id", "=", int(task_id)],
                        ["message_type", "in", ["comment", "email"]],
                    ],
                    ["author_id", "date", "body"],
                ],
                {"order": "date desc, id desc", "limit": 30},
            )
        except OdooConnectionError:
            raw = []
        for message in raw or []:
            if not (message.get("body") or "").strip():
                continue
            messages.append(
                TaskMessage(
                    author=_name(message.get("author_id")) or "Someone",
                    date=to_local_datetime(message.get("date")),
                    body=message.get("body") or "",
                )
            )

        return TaskDetail(
            id=record.get("id", int(task_id)),
            name=record.get("name") or "Untitled task",
            project=_name(record.get("project_id")) or "Private tasks",
            stage=_name(record.get("stage_id")) or "No stage",
            state=STATE_LABELS.get(record.get("state") or "", ""),
            done=self.is_done(record.get("state") or "", stage_pair, folded),
            deadline=to_local_date(record.get("date_deadline")),
            starred=str(record.get("priority") or "0") not in ("0", ""),
            description=record.get("description") or "",
            assignees=self._names(session, "res.users", record.get("user_ids") or []),
            tags=self._names(session, "project.tags", record.get("tag_ids") or []),
            customer=_name(record.get("partner_id")),
            milestone=_name(record.get("milestone_id")),
            parent=_name(record.get("parent_id")),
            created=to_local_date(record.get("create_date")),
            updated=to_local_date(record.get("write_date")),
            allocated_hours=float(record.get("allocated_hours") or 0),
            spent_hours=float(record.get("effective_hours") or 0),
            subtasks=tuple(subtasks),
            messages=tuple(messages),
        )

    def _json_rpc(self, endpoint, params):
        body = json.dumps({
            "jsonrpc": "2.0",
            "method": "call",
            "params": params,
            "id": 1,
        }).encode("utf-8")
        request = Request(
            endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "OdooDesktop/1.0",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 403:
                message = (
                    "Odoo returned HTTP 403 (Forbidden). The server or its security proxy "
                    "blocked the API request; ask your Odoo administrator to allow POST /jsonrpc."
                )
            elif exc.code == 401:
                message = "Odoo returned HTTP 401 (Unauthorized). Check the API endpoint and account access."
            else:
                message = f"Odoo returned HTTP {exc.code} ({exc.reason}). Check the server URL and API endpoint."
            raise OdooConnectionError(message) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise OdooConnectionError(
                "Could not reach Odoo. Check the URL and your network connection."
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OdooConnectionError("Odoo returned an unreadable response.") from exc

        if not isinstance(payload, dict):
            raise OdooConnectionError("Odoo returned an unexpected response.")
        if payload.get("error"):
            message = payload["error"].get("data", {}).get("message", "")
            if "database" in message.lower():
                raise OdooConnectionError(
                    "Odoo could not use that database. Check its name or enter it manually."
                )
            raise OdooConnectionError("Odoo rejected the request. Check the server settings.")
        if "result" not in payload:
            raise OdooConnectionError("Odoo returned an unexpected response.")
        return payload["result"]
