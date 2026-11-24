"""More data from Odoo than tasks: activities, timesheets, Discuss, meetings,
related tasks, milestones, time off, helpdesk tickets and CRM opportunities.

`OdooClient` here is the client the app uses: the core client plus these
calls. Most of them return `Row` items, ready for a table. Every call reads
only the fields this Odoo really has, so it keeps working across versions.
The two calls that write are `mark_activity_done` and `post_channel_message`.
"""

import datetime
from dataclasses import dataclass

from .odoo_client import CLOSED_STATES, TASK_FIELDS, OdooConnectionError, TaskMessage
from .odoo_client import OdooClient as _CoreClient

# Models that only exist when the matching Odoo app is installed.
OPTIONAL_MODELS = (
    "mail.activity",
    "account.analytic.line",
    "discuss.channel",
    "calendar.event",
    "project.milestone",
    "hr.leave",
    "helpdesk.ticket",
    "crm.lead",
)
PROJECT_STATUS = {
    "on_track": "On track",
    "at_risk": "At risk",
    "off_track": "Off track",
    "on_hold": "On hold",
    "done": "Done",
    "to_define": "Not set",
}
LEAVE_STATES = {
    "draft": "To submit",
    "confirm": "To approve",
    "validate1": "Second approval",
    "validate": "Approved",
}
PRIORITY_STARS = {"1": "Low", "2": "High", "3": "Urgent"}


@dataclass(frozen=True)
class Row:
    key: str  # unique inside its table
    cells: tuple
    model: str = ""  # record opened by a double-click
    res_id: int = 0
    tag: str = ""  # overdue | today | muted | ""


@dataclass(frozen=True)
class TimeLine:
    date: str  # YYYY-MM-DD
    hours: float
    project: str
    task: str
    name: str


@dataclass(frozen=True)
class Conversation:
    id: int
    name: str
    kind: str  # chat | group | channel
    unread: int = 0


@dataclass(frozen=True)
class Meeting:
    id: int
    name: str
    day: str  # local date, YYYY-MM-DD
    time: str  # local HH:MM, or "" for an all-day meeting
    location: str = ""


def _name(pair):
    """Name of an Odoo many2one value ([id, name] or False)."""
    return str(pair[1]) if isinstance(pair, (list, tuple)) and len(pair) > 1 else ""


def _day(text):
    try:
        return datetime.date.fromisoformat(str(text or "")[:10])
    except ValueError:
        return None


def short_date(text, today=None):
    day = _day(text)
    if day is None:
        return ""
    today = today or datetime.date.today()
    return day.strftime("%d %b" if day.year == today.year else "%d %b %Y")


def due_text(text, today):
    """('3 days late', 'overdue') style pair for a due date."""
    day = _day(text)
    if day is None:
        return "", ""
    delta = (day - today).days
    if delta < 0:
        return ("1 day late" if delta == -1 else f"{-delta} days late"), "overdue"
    if delta == 0:
        return "Today", "today"
    if delta == 1:
        return "Tomorrow", ""
    if delta <= 7:
        return f"in {delta} days", ""
    return short_date(text, today), ""


def to_local(text, tz=None):
    """Odoo's UTC 'YYYY-MM-DD HH:MM:SS' as a local datetime, or None."""
    try:
        moment = datetime.datetime.strptime(str(text or "")[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return moment.replace(tzinfo=datetime.timezone.utc).astimezone(tz)


def plain(html, limit=90):
    from . import richtext

    return " ".join(richtext.html_to_text(html or "").split())[:limit]


class OdooClient(_CoreClient):
    def __init__(self, timeout=20):
        super().__init__(timeout)
        self._model_field_cache = {}
        self._name_cache = {}

    # Helpers --------------------------------------------------------------

    def model_fields(self, session, model):
        key = (session.base_url, session.database, model)
        if key not in self._model_field_cache:
            fields = self.call_model(session, model, "fields_get", [], {"attributes": ["type"]})
            self._model_field_cache[key] = set(fields or {})
        return self._model_field_cache[key]

    def _search(self, session, model, domain, wanted, order=None, limit=None):
        """search_read limited to the fields this Odoo has."""
        available = self.model_fields(session, model)
        kwargs = {}
        if order:
            kwargs["order"] = order
        if limit:
            kwargs["limit"] = limit
        fields = [name for name in wanted if name in available]
        return self.call_model(session, model, "search_read", [domain, fields], kwargs) or []

    def available_models(self, session, names=OPTIONAL_MODELS):
        """Which optional models exist here. When Odoo will not say, assume all of them."""
        try:
            records = self.call_model(
                session, "ir.model", "search_read", [[["model", "in", list(names)]], ["model"]]
            )
        except OdooConnectionError:
            return set(names)
        return {record.get("model") for record in records or []}

    def my_name(self, session):
        key = (session.base_url, session.database, session.uid)
        if key not in self._name_cache:
            try:
                records = self.call_model(session, "res.users", "read", [[session.uid], ["name"]])
            except OdooConnectionError:
                records = []
            self._name_cache[key] = (records[0].get("name") or "") if records else ""
        return self._name_cache[key]

    def _user_names(self, session, user_ids):
        if not user_ids:
            return {}
        try:
            users = self.call_model(session, "res.users", "read", [sorted(user_ids), ["display_name"]])
        except OdooConnectionError:
            return {}
        return {user["id"]: user.get("display_name") or "" for user in users or []}

    def _open_task_domain(self, session):
        available = self.task_fields(session)
        if "state" in available:
            return [["state", "not in", list(CLOSED_STATES)]]
        if "is_closed" in available:
            return [["is_closed", "=", False]]
        return []

    # Activities -------------------------------------------------------------

    def fetch_activities(self, session, today=None):
        """My scheduled activities on any record, most urgent first."""
        today = today or datetime.date.today()
        records = self._search(
            session,
            "mail.activity",
            [["user_id", "=", session.uid]],
            ["res_model", "res_id", "res_name", "activity_type_id", "summary", "note", "date_deadline"],
            order="date_deadline asc, id asc",
            limit=300,
        )
        rows = []
        for record in records:
            due, tag = due_text(record.get("date_deadline"), today)
            what = record.get("summary") or plain(record.get("note")) or ""
            rows.append(
                Row(
                    key=str(record["id"]),
                    cells=(due, _name(record.get("activity_type_id")) or "To do", what, record.get("res_name") or ""),
                    model=record.get("res_model") or "",
                    res_id=record.get("res_id") or 0,
                    tag=tag,
                )
            )
        return tuple(rows)

    def mark_activity_done(self, session, activity_id, feedback=""):
        return self.call_model(
            session, "mail.activity", "action_feedback", [[int(activity_id)]], {"feedback": feedback.strip() or False}
        )

    # Timesheets -------------------------------------------------------------

    def fetch_timesheets(self, session, start, end):
        """My timesheet lines between two dates (YYYY-MM-DD, inclusive)."""
        records = self._search(
            session,
            "account.analytic.line",
            [["user_id", "=", session.uid], ["project_id", "!=", False], ["date", ">=", start], ["date", "<=", end]],
            ["date", "unit_amount", "project_id", "task_id", "name"],
            order="date asc, id asc",
            limit=1000,
        )
        return tuple(
            TimeLine(
                date=str(record.get("date") or "")[:10],
                hours=float(record.get("unit_amount") or 0),
                project=_name(record.get("project_id")) or "No project",
                task=_name(record.get("task_id")) or "No task",
                name=record.get("name") or "",
            )
            for record in records
        )

    # Tasks around mine --------------------------------------------------------

    def fetch_followed_tasks(self, session, today=None):
        """Open tasks I created or follow that are not assigned to me."""
        today = today or datetime.date.today()
        partner = self.my_partner_id(session)
        domain = [["user_ids", "not in", [session.uid]]] + self._open_task_domain(session)
        domain += ["|", ["create_uid", "=", session.uid], ["message_partner_ids", "in", [partner]]]
        wanted = [name for name in TASK_FIELDS + ("user_ids",) if name in self.task_fields(session)]
        records = self.call_model(
            session, "project.task", "search_read", [domain, wanted], {"order": "id desc", "limit": 300}
        ) or []
        folded = self.folded_stages(session, [r["stage_id"][0] for r in records if r.get("stage_id")])
        names = self._user_names(session, {uid for r in records for uid in r.get("user_ids") or []})
        rows = []
        for record in records:
            info = self._task_info(record, folded)
            if info.done:
                continue
            due, tag = due_text(info.deadline, today)
            people = ", ".join(names.get(uid) or f"User {uid}" for uid in record.get("user_ids") or [])
            rows.append(
                Row(str(info.id), (info.name, info.project, info.stage, people or "Nobody", due), "project.task", info.id, tag)
            )
        return tuple(rows)

    def fetch_dependencies(self, session):
        """(my tasks waiting on someone else's task, other tasks waiting on mine)."""
        available = self.task_fields(session)
        if "depend_on_ids" not in available:
            raise OdooConnectionError("Task dependencies are not available on this Odoo.")
        fields = ["name", "project_id", "stage_id", "state", "depend_on_ids", "dependent_ids"]
        wanted = [name for name in fields if name in available]
        domain = [["user_ids", "in", [session.uid]]] + self._open_task_domain(session)
        mine = self.call_model(
            session, "project.task", "search_read", [domain, wanted], {"order": "id desc", "limit": 2000}
        ) or []
        linked = sorted({i for r in mine for i in (r.get("depend_on_ids") or []) + (r.get("dependent_ids") or [])})
        if not linked:
            return (), ()
        other_fields = [name for name in ("name", "project_id", "stage_id", "state", "user_ids") if name in available]
        others = self.call_model(
            session, "project.task", "search_read", [[["id", "in", linked]], other_fields], {"limit": 2000}
        ) or []
        folded = self.folded_stages(
            session, [r["stage_id"][0] for r in list(mine) + list(others) if r.get("stage_id")]
        )
        names = self._user_names(session, {uid for r in others for uid in r.get("user_ids") or []})
        by_id = {record["id"]: record for record in others}

        def is_open(record):
            return not self.is_done(record.get("state") or "", record.get("stage_id"), folded)

        def people(record):
            return ", ".join(names.get(uid) or f"User {uid}" for uid in record.get("user_ids") or []) or "Nobody"

        waiting, blocking = [], []
        for task in mine:
            if not is_open(task):
                continue
            for other_id in task.get("depend_on_ids") or []:
                other = by_id.get(other_id)
                if other and is_open(other):
                    waiting.append(
                        Row(
                            f"{task['id']}:{other_id}",
                            (task.get("name") or "", other.get("name") or "", people(other),
                             _name(other.get("stage_id")), _name(task.get("project_id"))),
                            "project.task", other_id,
                        )  # fmt: skip
                    )
            for other_id in task.get("dependent_ids") or []:
                other = by_id.get(other_id)
                if other and is_open(other):
                    blocking.append(
                        Row(
                            f"{other_id}:{task['id']}",
                            (other.get("name") or "", people(other), task.get("name") or "",
                             _name(task.get("stage_id")), _name(task.get("project_id"))),
                            "project.task", other_id,
                        )  # fmt: skip
                    )
        return tuple(waiting), tuple(blocking)

    def fetch_milestones(self, session, project_ids, today=None):
        """Open milestones of my projects, with the status the project manager last posted."""
        today = today or datetime.date.today()
        ids = sorted({int(i) for i in project_ids if i})
        if not ids:
            return ()
        projects = self._search(session, "project.project", [["id", "in", ids]], ["name", "last_update_status"])
        status = {p["id"]: PROJECT_STATUS.get(p.get("last_update_status") or "", "") for p in projects}
        try:
            milestones = self._search(
                session,
                "project.milestone",
                [["project_id", "in", ids], ["is_reached", "=", False]],
                ["name", "project_id", "deadline"],
                order="deadline asc, id asc",
                limit=500,
            )
        except OdooConnectionError:
            milestones = []
        rows, covered = [], set()
        for record in sorted(milestones, key=lambda r: str(r.get("deadline") or "9999")):
            project = record.get("project_id") or [0, ""]
            covered.add(project[0])
            due, tag = due_text(record.get("deadline"), today)
            rows.append(
                Row(
                    f"m:{record['id']}",
                    (_name(project), record.get("name") or "", due or "No date", status.get(project[0], "")),
                    "project.project", project[0], tag,
                )  # fmt: skip
            )
        for project in sorted(projects, key=lambda p: str(p.get("name") or "").lower()):
            if project["id"] not in covered:
                rows.append(
                    Row(
                        f"p:{project['id']}",
                        (project.get("name") or "", "No open milestone", "", status.get(project["id"], "")),
                        "project.project", project["id"], "muted",
                    )  # fmt: skip
                )
        return tuple(rows)

    # Discuss ------------------------------------------------------------------

    def fetch_conversations(self, session):
        """Channels and direct messages I am a member of, unread first."""
        partner = self.my_partner_id(session)
        if not partner:
            return ()
        members = self._search(
            session,
            "discuss.channel.member",
            [["partner_id", "=", partner]],
            ["channel_id", "message_unread_counter"],
            limit=300,
        )
        unread = {m["channel_id"][0]: int(m.get("message_unread_counter") or 0) for m in members if m.get("channel_id")}
        if not unread:
            return ()
        channels = self._search(
            session, "discuss.channel", [["id", "in", sorted(unread)]], ["name", "display_name", "channel_type"]
        )
        me = self.my_name(session)
        result = []
        for channel in channels:
            kind = channel.get("channel_type") or "channel"
            name = channel.get("name") or channel.get("display_name") or ""
            if kind in ("chat", "group"):
                people = [part.strip() for part in (channel.get("display_name") or name).split(",")]
                name = ", ".join(part for part in people if part and part != me) or name or "Direct message"
            else:
                name = "# " + (name or "channel")
            result.append(Conversation(channel["id"], name, kind, unread.get(channel["id"], 0)))
        result.sort(key=lambda c: (-c.unread, c.kind == "channel", c.name.lower()))
        return tuple(result)

    def fetch_channel_messages(self, session, channel_id, limit=40):
        """Latest messages of a conversation, oldest first."""
        records = self.call_model(
            session,
            "mail.message",
            "search_read",
            [
                [["model", "=", "discuss.channel"], ["res_id", "=", int(channel_id)],
                 ["message_type", "in", ["comment", "email"]]],
                ["author_id", "date", "body"],
            ],
            {"order": "id desc", "limit": limit},
        ) or []  # fmt: skip
        messages = []
        for record in reversed(records):
            moment = to_local(record.get("date"))
            messages.append(
                TaskMessage(
                    author=_name(record.get("author_id")) or "Someone",
                    date=moment.strftime("%Y-%m-%d %H:%M") if moment else "",
                    body=record.get("body") or "",
                )
            )
        return tuple(messages)

    def post_channel_message(self, session, channel_id, text):
        text = (text or "").strip()
        if not text:
            raise OdooConnectionError("Write a message first.")
        return self.call_model(
            session,
            "discuss.channel",
            "message_post",
            [[int(channel_id)]],
            {"body": text, "message_type": "comment", "subtype_xmlid": "mail.mt_comment"},
        )

    # Meetings -------------------------------------------------------------------

    def fetch_meetings(self, session, start, end, tz=None):
        """Calendar meetings I attend between two dates (YYYY-MM-DD, inclusive)."""
        partner = self.my_partner_id(session)
        if not partner:
            return ()
        before = (datetime.date.fromisoformat(start) - datetime.timedelta(days=1)).isoformat()
        after = (datetime.date.fromisoformat(end) + datetime.timedelta(days=1)).isoformat()
        records = self._search(
            session,
            "calendar.event",
            [["partner_ids", "in", [partner]], ["start", "<=", f"{after} 23:59:59"], ["stop", ">=", f"{before} 00:00:00"]],
            ["name", "start", "stop", "allday", "start_date", "location"],
            order="start asc",
            limit=500,
        )
        meetings = []
        for record in records:
            if record.get("allday"):
                day, time = str(record.get("start_date") or record.get("start") or "")[:10], ""
            else:
                moment = to_local(record.get("start"), tz)
                if moment is None:
                    continue
                day, time = moment.date().isoformat(), moment.strftime("%H:%M")
            if start <= day <= end:
                meetings.append(
                    Meeting(record["id"], record.get("name") or "Meeting", day, time, record.get("location") or "")
                )
        meetings.sort(key=lambda m: (m.day, m.time))
        return tuple(meetings)

    # Time off ---------------------------------------------------------------------

    def fetch_time_off(self, session, today=None):
        """My balances, my coming time off, and who is away today."""
        today = today or datetime.date.today()
        rows, failures, last_error = [], 0, None

        def part(work):
            nonlocal failures, last_error
            try:
                rows.extend(work())
            except OdooConnectionError as exc:
                failures += 1
                last_error = exc

        def balances():
            types = self._search(
                session, "hr.leave.type", [],
                ["name", "virtual_remaining_leaves", "max_leaves", "requires_allocation", "request_unit"],
                limit=100,
            )  # fmt: skip
            for record in types:
                total = float(record.get("max_leaves") or 0)
                if total <= 0:
                    continue
                unit = "hours" if record.get("request_unit") == "hour" else "days"
                left = float(record.get("virtual_remaining_leaves") or 0)
                yield Row(
                    f"b:{record['id']}",
                    ("Balance", record.get("name") or "", f"{left:g} of {total:g} {unit} left", ""),
                    tag="overdue" if left < 0 else "",
                )

        def mine():
            leaves = self._search(
                session, "hr.leave",
                [["user_id", "=", session.uid], ["date_to", ">=", f"{today.isoformat()} 00:00:00"],
                 ["state", "not in", ["refuse", "cancel"]]],
                ["holiday_status_id", "request_date_from", "request_date_to", "date_from", "date_to",
                 "number_of_days", "state"],
                order="date_from asc", limit=50,
            )  # fmt: skip
            for record in leaves:
                start = record.get("request_date_from") or record.get("date_from")
                end = record.get("request_date_to") or record.get("date_to")
                dates = short_date(start, today)
                if str(end or "")[:10] != str(start or "")[:10]:
                    dates += "  →  " + short_date(end, today)
                detail = f"{float(record.get('number_of_days') or 0):g} day(s)  ·  " + LEAVE_STATES.get(
                    record.get("state") or "", str(record.get("state") or "")
                )
                yield Row(
                    f"l:{record['id']}", ("My time off", _name(record.get("holiday_status_id")), detail, dates),
                    "hr.leave", record["id"],
                )  # fmt: skip

        def away():
            people = self._search(
                session, "hr.employee.public", [["is_absent", "=", True]], ["name", "leave_date_to"],
                order="name asc", limit=200,
            )  # fmt: skip
            for record in people:
                until = short_date(record.get("leave_date_to"), today)
                yield Row(
                    f"a:{record['id']}", ("Away today", record.get("name") or "", "", f"until {until}" if until else ""),
                    tag="muted",
                )  # fmt: skip

        for work in (balances, mine, away):
            part(lambda work=work: list(work()))
        if failures == 3 and last_error:
            raise last_error
        return tuple(rows)

    # Helpdesk and CRM -----------------------------------------------------------------

    def fetch_tickets(self, session, today=None):
        today = today or datetime.date.today()
        records = self._search(
            session,
            "helpdesk.ticket",
            [["user_id", "=", session.uid], ["stage_id.fold", "=", False]],
            ["name", "partner_id", "team_id", "stage_id", "priority", "sla_deadline"],
            order="priority desc, id desc",
            limit=300,
        )
        rows = []
        for record in records:
            due, tag = due_text(to_local_date_text(record.get("sla_deadline")), today)
            rows.append(
                Row(
                    str(record["id"]),
                    (record.get("name") or "", _name(record.get("partner_id")), _name(record.get("team_id")),
                     _name(record.get("stage_id")), PRIORITY_STARS.get(str(record.get("priority") or "0"), ""), due),
                    "helpdesk.ticket", record["id"], tag,
                )  # fmt: skip
            )
        return tuple(rows)

    def fetch_opportunities(self, session, today=None):
        today = today or datetime.date.today()
        records = self._search(
            session,
            "crm.lead",
            [["user_id", "=", session.uid], ["type", "=", "opportunity"], ["probability", "<", 100]],
            ["name", "partner_id", "stage_id", "expected_revenue", "activity_date_deadline", "activity_summary",
             "date_deadline"],
            order="expected_revenue desc, id desc",
            limit=300,
        )  # fmt: skip
        rows = []
        for record in records:
            due, tag = due_text(record.get("activity_date_deadline"), today)
            step = "  ·  ".join(part for part in (due, record.get("activity_summary") or "") if part)
            rows.append(
                Row(
                    str(record["id"]),
                    (record.get("name") or "", _name(record.get("partner_id")), _name(record.get("stage_id")),
                     f"{float(record.get('expected_revenue') or 0):,.0f}", step or "Nothing planned",
                     short_date(record.get("date_deadline"), today)),
                    "crm.lead", record["id"], tag,
                )  # fmt: skip
            )
        return tuple(rows)


def to_local_date_text(text):
    moment = to_local(text)
    return moment.date().isoformat() if moment else ""
