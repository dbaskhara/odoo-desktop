"""Pages fed by more Odoo data: Activities, Timesheets, Discuss and "More from Odoo"
(related tasks, milestones, time off, helpdesk, CRM), plus meetings on the Calendar.

Mixed into DashboardView. Each page loads when it is opened, in the background.
A page whose Odoo app is missing is hidden from the menu, or says so when opened.
"""

import datetime
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

from . import insights, richtext, theme
from .odoo_client import OdooConnectionError
from .theme import Card, PillButton

# Menu entries that need an optional Odoo model.
NAV_MODELS = {"activities": "mail.activity", "timesheets": "account.analytic.line", "discuss": "discuss.channel"}

# key, label, model needed, columns (heading, width), text when empty
MORE = (
    ("following", "Tasks I follow or created", None,
     (("TASK", 280), ("PROJECT", 170), ("STAGE", 130), ("ASSIGNED TO", 170), ("DEADLINE", 110)),
     "No open task that you created or follow without being assigned to it."),
    ("waiting", "My tasks waiting on others", None,
     (("MY TASK", 240), ("WAITS FOR", 240), ("ASSIGNED TO", 150), ("THEIR STAGE", 120), ("PROJECT", 150)),
     "None of your tasks is blocked by another open task."),
    ("blocking", "Tasks waiting on me", None,
     (("THEIR TASK", 240), ("ASSIGNED TO", 150), ("WAITS FOR MY TASK", 240), ("MY STAGE", 120), ("PROJECT", 150)),
     "Nobody is blocked by one of your tasks."),
    ("milestones", "Milestones and project status", None,
     (("PROJECT", 220), ("MILESTONE", 300), ("DEADLINE", 140), ("PROJECT STATUS", 140)),
     "Your projects have no milestone and no posted status."),
    ("timeoff", "Time off", "hr.leave",
     (("KIND", 130), ("NAME", 260), ("DETAIL", 260), ("DATES", 200)),
     "No balance, no coming time off and nobody away today."),
    ("tickets", "Helpdesk tickets", "helpdesk.ticket",
     (("TICKET", 280), ("CUSTOMER", 170), ("TEAM", 130), ("STAGE", 120), ("PRIORITY", 90), ("SLA DEADLINE", 110)),
     "No open ticket is assigned to you."),
    ("crm", "CRM pipeline", "crm.lead",
     (("OPPORTUNITY", 260), ("CUSTOMER", 170), ("STAGE", 120), ("EXPECTED", 100), ("NEXT STEP", 200), ("CLOSING", 90)),
     "No open opportunity is assigned to you."),
)  # fmt: skip
MORE_BY_KEY = {spec[0]: spec for spec in MORE}
MAX_COLUMNS = 6
ERROR_PREFIX = "Not available on this Odoo.  "


def hours_text(hours):
    minutes = round(float(hours or 0) * 60)
    return f"{minutes // 60}:{minutes % 60:02d}" if minutes else ""


class OdooPagesMixin:
    # Shared -------------------------------------------------------------

    def _init_odoo_state(self):
        self.available = None  # optional models this Odoo has; None until asked
        self.activities = ()
        self._activities_loaded = False
        self.week_start = insights.week_range(datetime.date.today())[0]
        self.timelines = ()
        self._timesheets_week = None
        self.conversations = ()
        self._conversations_loaded = False
        self.channel_id = None
        self.channel_messages = ()
        self.more_key = "following"
        self.more_rows = {}
        self.meetings = {}  # (year, month) -> meetings of that calendar page

    def _odoo_load(self, kind, work, page=None, quiet=False):
        """Run an Odoo read in the background. Errors go to the page, or nowhere when quiet."""

        def run():
            try:
                self.results.put((kind, work()))
            except OdooConnectionError as exc:
                if not quiet:
                    self.results.put(("odoo_error", (page or kind, str(exc))))
            except Exception:
                if not quiet:
                    self.results.put(("odoo_error", (page or kind, "Could not load this from Odoo.")))

        threading.Thread(target=run, daemon=True).start()

    def has_model(self, model):
        return model is None or self.available is None or model in self.available

    def _odoo_after_load(self):
        """After each refresh of the tasks: keep the menu counters up to date."""
        if self.available is None:
            self._odoo_load("models", lambda: self.client.available_models(self.session), quiet=True)
        else:
            self._odoo_counts()

    def _odoo_counts(self):
        if self.has_model("mail.activity"):
            self.load_activities(quiet=True)
        if self.has_model("discuss.channel"):
            self.load_conversations(quiet=True)
        self.meetings.pop((self.cal_year, self.cal_month), None)
        self._load_meetings()
        self._load_today_extras()

    def _on_show_odoo(self, key):
        if key == "activities" and not self._activities_loaded:
            self.load_activities()
        elif key == "timesheets" and self._timesheets_week != self.week_start:
            self.load_timesheets()
        elif key == "discuss" and not self._conversations_loaded:
            self.load_conversations()
        elif key == "more" and self.more_key not in self.more_rows:
            self.show_more(self.more_key)
        elif key == "calendar":
            self._load_meetings()

    def _apply_availability(self):
        """Hide the menu entries and choices whose Odoo app is not installed."""
        if self.available is None:
            return
        for key, model in NAV_MODELS.items():
            if model not in self.available and key in self.nav_rows:
                self.nav_rows[key][0].master.pack_forget()
        labels = [label for _key, label, model, _cols, _empty in MORE if self.has_model(model)]
        self.more_box.configure(values=labels)

    def _render_odoo(self):
        self._apply_availability()
        if self._activities_loaded:
            self._render_activities()
        if self._timesheets_week is not None:
            self._render_timesheets()
        if self._conversations_loaded:
            self._render_conversations()
            self._render_channel()
        if self.more_key in self.more_rows:
            self._render_more()

    def _handle_odoo_result(self, kind, payload):
        if kind == "models":
            self.available = set(payload)
            self._apply_availability()
            self._odoo_counts()
        elif kind == "activities":
            self.activities = payload
            self._activities_loaded = True
            self._render_activities()
        elif kind == "timesheets":
            week, lines = payload
            if week == self.week_start:
                self.timelines = lines
                self._timesheets_week = week
                self._render_timesheets()
        elif kind == "conversations":
            self.conversations = payload
            self._conversations_loaded = True
            self._notify_new_messages()
            self._render_conversations()
        elif kind == "channel":
            channel_id, messages = payload
            if channel_id == self.channel_id:
                self.channel_messages = messages
                self._render_channel()
                conversation = self._conversation()
                if conversation is not None and conversation.unread and self.current_tab == "discuss":
                    self._mark_channel_read(channel_id)
        elif kind == "more":
            key, rows = payload
            self.more_rows[key] = rows
            if key == self.more_key:
                self._render_more()
        elif kind == "meetings":
            month, meetings = payload
            self.meetings[month] = meetings
            if month == (self.cal_year, self.cal_month):
                self._render_calendar()
        elif kind == "odoo_error":
            self._show_odoo_error(*payload)
        else:
            return self._handle_power_result(kind, payload)
        return True

    def _show_odoo_error(self, page, message):
        text = ERROR_PREFIX + message
        if page == "activities":
            self.activities_sub.set(text)
        elif page == "timesheets":
            self.timesheets_sub.set(text)
        elif page in ("conversations", "channel"):
            self.discuss_sub.set(text)
        elif page == "more":
            self.more_sub.set(text)
            self.more_tree.delete(*self.more_tree.get_children())
        else:
            self._set_status(message, error=True)

    def open_record(self, model, res_id):
        """Tasks open inside the app, every other record in the browser."""
        if not model or not res_id:
            return
        if model == "project.task":
            self.open_task(res_id)
        else:
            webbrowser.open(f"{self.session.base_url}/web#id={int(res_id)}&model={model}&view_type=form")

    def _table(self, parent, columns, tree_column=None):
        """A list with our row colours. columns: (name, heading, width, stretch)."""
        box = ttk.Frame(parent)
        box.pack(fill="both", expand=True)
        tree = ttk.Treeview(
            box, columns=[name for name, *_rest in columns], show="tree headings" if tree_column else "headings"
        )
        if tree_column:
            tree.heading("#0", text=tree_column[0], anchor="w")
            tree.column("#0", width=tree_column[1], anchor="w", stretch=True)
        for name, heading, width, stretch in columns:
            tree.heading(name, text=heading, anchor="w")
            tree.column(name, width=width, anchor="w", stretch=stretch)
        tree.tag_configure("overdue", foreground=theme.DANGER)
        tree.tag_configure("today", foreground=theme.WARN)
        tree.tag_configure("muted", foreground=theme.FAINT)
        tree.tag_configure("group", font=(theme.FONT_BOLD, 9), foreground=theme.TEXT, background=theme.SURFACE_2)
        tree.pack(side="left", fill="both", expand=True)
        theme.attach_scrollbar(tree, box)
        return tree

    @staticmethod
    def _fill(tree, rows):
        tree.delete(*tree.get_children())
        for row in rows:
            tree.insert("", "end", iid=row.key, values=row.cells, tags=(row.tag,) if row.tag else ())

    # Activities -----------------------------------------------------------

    def _build_activities_page(self, page):
        self.activities_sub = tk.StringVar(value="What Odoo scheduled for you, on any record.")
        self._page_header(
            page, "Activities", self.activities_sub, action=("Refresh", self.load_activities, "secondary", "refresh")
        )
        card = Card(page, padding=(16, 10))
        card.pack(fill="both", expand=True)
        tools = ttk.Frame(card.body)
        tools.pack(fill="x", pady=(2, 8))
        ttk.Label(tools, text="Double-click an activity to open its record.", style="Hint.TLabel").pack(side="left")
        PillButton(tools, "Mark done", self.mark_activity_clicked, kind="primary", height=32).pack(side="right")
        self.activities_tree = self._table(
            card.body,
            (("due", "DUE", 110, False), ("type", "TYPE", 130, False), ("what", "WHAT", 300, True), ("on", "ON", 300, True)),
        )
        self.activities_tree.bind("<Double-1>", lambda _e: self._open_activity())

    def load_activities(self, quiet=False):
        if not quiet:
            self.activities_sub.set("Loading activities…")
        today = self._today()
        self._odoo_load("activities", lambda: self.client.fetch_activities(self.session, today), quiet=quiet)

    def _render_activities(self):
        rows = self.activities
        late = sum(1 for row in rows if row.tag == "overdue")
        now = sum(1 for row in rows if row.tag == "today")
        urgent = late + now
        self.tab_text["activities"].set(f"Activities ({urgent})" if urgent else "Activities")
        self.activities_sub.set(
            f"{len(rows)} planned for you: {late} late, {now} due today." if rows else "No activity is planned for you."
        )
        self._fill(self.activities_tree, rows)
        self._render_odoo_strip()

    def _selected_activity(self):
        selection = self.activities_tree.selection()
        return next((row for row in self.activities if selection and row.key == selection[0]), None)

    def _open_activity(self):
        row = self._selected_activity()
        if row:
            self.open_record(row.model, row.res_id)

    def mark_activity_clicked(self):
        row = self._selected_activity()
        if row is None:
            self.activities_sub.set("Select an activity first.")
            return
        label = row.cells[2] or row.cells[1]
        answer = self._form(
            "Mark activity done",
            [("note", f"Done: “{label}” on {row.cells[3]}.  Note for Odoo (optional)", "text", "", None)],
            ok_text="Mark done",
        )
        if answer is not None:
            self._busy(lambda: self.do_mark_activity(row.key, answer.get("note", "")))

    def do_mark_activity(self, key, note=""):
        self.client.mark_activity_done(self.session, int(key), note)
        self.activities = tuple(row for row in self.activities if row.key != key)
        self._render_activities()
        self._set_status("Activity marked as done in Odoo.")

    # Timesheets ---------------------------------------------------------------

    def _build_timesheets_page(self, page):
        self.timesheets_sub = tk.StringVar(value="The hours you logged this week.")
        head = self._page_header(page, "Timesheets", self.timesheets_sub)
        PillButton(
            head, "", lambda: self.shift_week(1), kind="secondary", outer=theme.BG, icon=theme.ICONS["next"], width=40
        ).pack(side="right")
        PillButton(head, "This week", lambda: self.shift_week(0), kind="secondary", outer=theme.BG).pack(
            side="right", padx=8
        )
        PillButton(
            head, "", lambda: self.shift_week(-1), kind="secondary", outer=theme.BG, icon=theme.ICONS["prev"], width=40
        ).pack(side="right")
        card = Card(page, padding=(16, 12))
        card.pack(fill="both", expand=True)
        columns = [(f"d{i}", "", 74, False) for i in range(7)] + [("total", "TOTAL", 80, False)]
        self.timesheets_tree = self._table(card.body, columns, tree_column=("PROJECT / TASK", 380))

    def shift_week(self, delta):
        if delta == 0:
            self.week_start = insights.week_range(self._today())[0]
        else:
            self.week_start += datetime.timedelta(days=7 * delta)
        self.load_timesheets()

    def load_timesheets(self):
        week = self.week_start
        end = week + datetime.timedelta(days=6)
        self.timesheets_sub.set("Loading timesheets…")
        self._odoo_load(
            "timesheets",
            lambda: (week, self.client.fetch_timesheets(self.session, week.isoformat(), end.isoformat())),
        )

    def timesheet_table(self):
        """[(project, [hours per day], [(task, [hours per day])])] and the totals per day."""
        days = [(self.week_start + datetime.timedelta(days=i)).isoformat() for i in range(7)]
        projects = {}
        totals = [0.0] * 7
        for line in self.timelines:
            if line.date not in days:
                continue
            index = days.index(line.date)
            tasks = projects.setdefault(line.project, {})
            tasks.setdefault(line.task, [0.0] * 7)[index] += line.hours
            totals[index] += line.hours
        table = []
        for project in sorted(projects, key=str.lower):
            tasks = sorted(projects[project].items(), key=lambda item: item[0].lower())
            sums = [sum(hours[i] for _task, hours in tasks) for i in range(7)]
            table.append((project, sums, tasks))
        return table, totals

    def _render_timesheets(self):
        tree = self.timesheets_tree
        tree.delete(*tree.get_children())
        today = self._today()
        for index in range(7):
            day = self.week_start + datetime.timedelta(days=index)
            tree.heading(f"d{index}", text=day.strftime("%a %d").upper() + (" •" if day == today else ""), anchor="w")
        table, totals = self.timesheet_table()
        end = self.week_start + datetime.timedelta(days=6)
        span = f"{self.week_start.strftime('%d %b')} – {end.strftime('%d %b %Y')}"
        total = hours_text(sum(totals)) or "0:00"
        self.timesheets_sub.set(f"Week of {span}  ·  {total} logged" if table else f"Week of {span}  ·  nothing logged")

        def cells(hours):
            return [hours_text(value) for value in hours] + [hours_text(sum(hours))]

        for number, (project, sums, tasks) in enumerate(table):
            parent = tree.insert("", "end", iid=f"p{number}", text=project, values=cells(sums), open=True, tags=("group",))
            for index, (task, hours) in enumerate(tasks):
                tree.insert(parent, "end", iid=f"p{number}t{index}", text=task, values=cells(hours))
        if table:
            tree.insert("", "end", iid="total", text="TOTAL", values=cells(totals), tags=("group",))

    # Discuss ----------------------------------------------------------------------

    def _build_discuss_page(self, page):
        self.discuss_sub = tk.StringVar(value="Your Odoo channels and direct messages.")
        head = self._page_header(
            page, "Discuss", self.discuss_sub, action=("Refresh", self.load_conversations, "secondary", "refresh")
        )
        self.discuss_ai_button = PillButton(head, "Ask AI", self.discuss_ai_clicked, kind="secondary", outer=theme.BG)
        self.discuss_ai_button.pack(side="right", padx=(0, 8))
        body = tk.Frame(page, bg=theme.BG)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        left = Card(body, padding=(10, 10), fixed_width=310)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        self.conversations_tree = self._table(
            left.body, (("unread", "NEW", 50, False),), tree_column=("CONVERSATION", 200)
        )
        self.conversations_tree.tag_configure("unread", font=(theme.FONT_BOLD, 10), foreground=theme.TEXT)
        self.conversations_tree.bind("<<TreeviewSelect>>", lambda _e: self._conversation_selected())

        right = Card(body, padding=(4, 4))
        right.grid(row=0, column=1, sticky="nsew")
        self.channel_title = tk.StringVar(value="Select a conversation")
        ttk.Label(right.body, textvariable=self.channel_title, style="Heading.TLabel").pack(
            anchor="w", padx=14, pady=(10, 8)
        )
        tk.Frame(right.body, bg=theme.BORDER, height=1).pack(fill="x", padx=12)
        row = tk.Frame(right.body, bg=theme.SURFACE)
        row.pack(side="bottom", fill="x", padx=12, pady=10)
        self.channel_reply = tk.Text(
            row, height=2, width=10, wrap="word", relief="flat", font=(theme.FONT, 10), fg=theme.TEXT,
            bg=theme.SURFACE_2, insertbackground=theme.TEXT, highlightthickness=1,
            highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT, padx=10, pady=7,
        )  # fmt: skip
        self.channel_reply.pack(side="left", fill="x", expand=True)
        PillButton(row, "Send", self.send_channel_clicked, kind="primary", icon=theme.ICONS["send"], height=40).pack(
            side="left", padx=(10, 0), anchor="s"
        )
        tk.Frame(right.body, bg=theme.BORDER, height=1).pack(side="bottom", fill="x", padx=12)
        box = tk.Frame(right.body, bg=theme.SURFACE)
        box.pack(fill="both", expand=True)
        self.channel_text = tk.Text(
            box, wrap="word", relief="flat", font=(theme.FONT, 10), fg=theme.TEXT, bg=theme.SURFACE,
            highlightthickness=0, padx=16, pady=10, state="disabled", cursor="arrow", spacing1=1, spacing3=1,
        )  # fmt: skip
        self.channel_text.tag_configure("author", font=(theme.FONT_BOLD, 10), foreground=theme.TEXT, spacing1=10)
        self.channel_text.tag_configure("date", font=(theme.FONT, 8), foreground=theme.FAINT)
        self.channel_text.tag_configure("body", foreground=theme.TEXT, lmargin1=0, lmargin2=0)
        self.channel_text.tag_configure("muted", foreground=theme.MUTED)
        self.channel_text.pack(side="left", fill="both", expand=True)
        theme.attach_scrollbar(self.channel_text, box)

    def load_conversations(self, quiet=False):
        if not quiet:
            self.discuss_sub.set("Loading conversations…")
        self._odoo_load("conversations", lambda: self.client.fetch_conversations(self.session), quiet=quiet)
        if self.channel_id is not None:
            self._load_channel(quiet=True)

    def _render_conversations(self):
        tree = self.conversations_tree
        tree.delete(*tree.get_children())
        unread = sum(c.unread for c in self.conversations)
        self.tab_text["discuss"].set(f"Discuss ({unread})" if unread else "Discuss")
        self.discuss_sub.set(
            f"{unread} unread message(s) in {sum(1 for c in self.conversations if c.unread)} conversation(s)."
            if unread
            else ("Nothing unread." if self.conversations else "You are in no conversation.")
        )
        for conversation in self.conversations:
            tree.insert(
                "", "end", iid=str(conversation.id), text=conversation.name,
                values=(conversation.unread or "",), tags=("unread",) if conversation.unread else (),
            )  # fmt: skip
        if self.channel_id is not None and tree.exists(str(self.channel_id)):
            tree.selection_set(str(self.channel_id))
        self._render_odoo_strip()

    def _conversation_selected(self):
        selection = self.conversations_tree.selection()
        if not selection or int(selection[0]) == self.channel_id:
            return
        self.channel_id = int(selection[0])
        self.channel_messages = ()
        self._render_channel(loading=True)
        self._load_channel()

    def _load_channel(self, quiet=False):
        channel_id = self.channel_id
        self._odoo_load(
            "channel",
            lambda: (channel_id, self.client.fetch_channel_messages(self.session, channel_id)),
            page="channel",
            quiet=quiet,
        )

    def _conversation(self):
        return next((c for c in self.conversations if c.id == self.channel_id), None)

    def _render_channel(self, loading=False):
        conversation = self._conversation()
        self.channel_title.set(conversation.name if conversation else "Select a conversation")
        text = self.channel_text
        text.configure(state="normal")
        text.delete("1.0", "end")
        if conversation is None:
            text.insert("end", "Pick a conversation on the left to read it and answer.", "muted")
        elif loading:
            text.insert("end", "Loading…", "muted")
        elif not self.channel_messages:
            text.insert("end", "No message here yet.", "muted")
        for message in self.channel_messages:
            text.insert("end", message.author, "author")
            text.insert("end", f"   {message.date}\n", "date")
            text.insert("end", (richtext.html_to_text(message.body).strip() or "(attachment)") + "\n", "body")
        text.configure(state="disabled")
        text.see("end")

    def send_channel_clicked(self):
        conversation = self._conversation()
        message = self.channel_reply.get("1.0", "end").strip()
        if conversation is None or not message:
            return
        if self._confirm("Send message", f"Send this message to {conversation.name} in Odoo?"):
            self._busy(lambda: self.do_send_channel(message))

    def do_send_channel(self, message):
        self.client.post_channel_message(self.session, self.channel_id, message)
        self.channel_reply.delete("1.0", "end")
        self.channel_messages = self.client.fetch_channel_messages(self.session, self.channel_id)
        self._render_channel()
        self._set_status("Message sent.")

    # More from Odoo -----------------------------------------------------------------

    def _build_more_page(self, page):
        self.more_sub = tk.StringVar(value="Choose what to look at.")
        self._page_header(
            page, "More from Odoo", self.more_sub,
            action=("Refresh", lambda: self.show_more(self.more_key, reload=True), "secondary", "refresh"),
        )  # fmt: skip
        card = Card(page, padding=(16, 12))
        card.pack(fill="both", expand=True)
        tools = ttk.Frame(card.body)
        tools.pack(fill="x", pady=(0, 10))
        self.more_var = tk.StringVar(value=MORE_BY_KEY[self.more_key][1])
        self.more_box = ttk.Combobox(
            tools, textvariable=self.more_var, values=[spec[1] for spec in MORE], state="readonly", width=34,
            font=(theme.FONT, 10),
        )  # fmt: skip
        self.more_box.pack(side="left", ipady=3)
        self.more_box.bind("<<ComboboxSelected>>", lambda _e: self._more_chosen())
        ttk.Label(tools, text="Double-click a line to open it.", style="Hint.TLabel").pack(side="left", padx=(14, 0))
        self.more_buttons = ttk.Frame(tools)
        self.more_buttons.pack(side="right")
        tk.Frame(card.body, bg=theme.BORDER, height=1).pack(fill="x")
        self.more_tree = self._table(card.body, [(f"c{i}", "", 150, True) for i in range(MAX_COLUMNS)])
        self.more_tree.bind("<Double-1>", lambda _e: self._open_more())
        self._set_more_columns()

    def _set_more_columns(self):
        columns = MORE_BY_KEY[self.more_key][3]
        self.more_tree.configure(displaycolumns=[f"c{i}" for i in range(len(columns))])
        for index, (heading, width) in enumerate(columns):
            self.more_tree.heading(f"c{index}", text=heading, anchor="w")
            self.more_tree.column(f"c{index}", width=width, anchor="w", stretch=True)
        self._render_more_actions()

    def _more_chosen(self):
        key = next((spec[0] for spec in MORE if spec[1] == self.more_var.get()), None)
        if key:
            self.show_more(key)

    def more_loader(self, key):
        client, session, today = self.client, self.session, self._today()
        project_ids = {task.project_id for task in self.tasks}
        return {
            "following": lambda: client.fetch_followed_tasks(session, today),
            "waiting": lambda: client.fetch_dependencies(session)[0],
            "blocking": lambda: client.fetch_dependencies(session)[1],
            "milestones": lambda: client.fetch_milestones(session, project_ids, today),
            "timeoff": lambda: client.fetch_time_off(session, today),
            "tickets": lambda: client.fetch_tickets(session, today),
            "crm": lambda: client.fetch_opportunities(session, today),
        }[key]

    def show_more(self, key, reload=False):
        self.more_key = key
        self.more_var.set(MORE_BY_KEY[key][1])
        self._set_more_columns()
        if key in self.more_rows and not reload:
            self._render_more()
            return
        self.more_tree.delete(*self.more_tree.get_children())
        self.more_sub.set(f"Loading {MORE_BY_KEY[key][1].lower()}…")
        work = self.more_loader(key)
        self._odoo_load("more", lambda: (key, work()), page="more")

    def _render_more(self):
        _key, label, _model, columns, empty = MORE_BY_KEY[self.more_key]
        rows = self.more_rows.get(self.more_key, ())
        self._set_more_columns()
        self.more_sub.set(f"{label}: {len(rows)} line(s)." if rows else empty)
        tree = self.more_tree
        tree.delete(*tree.get_children())
        for row in rows:
            cells = tuple(row.cells) + ("",) * (MAX_COLUMNS - len(row.cells))
            tree.insert("", "end", iid=row.key, values=cells, tags=(row.tag,) if row.tag else ())

    def _open_more(self):
        selection = self.more_tree.selection()
        row = next((r for r in self.more_rows.get(self.more_key, ()) if selection and r.key == selection[0]), None)
        if row:
            self.open_record(row.model, row.res_id)

    # Meetings on the Calendar -------------------------------------------------------

    def _load_meetings(self):
        month = (self.cal_year, self.cal_month)
        if month in self.meetings or not self.has_model("calendar.event") or not hasattr(self.client, "fetch_meetings"):
            return
        weeks = insights.month_grid(*month)
        start, end = weeks[0][0].isoformat(), weeks[-1][-1].isoformat()
        self._odoo_load("meetings", lambda: (month, self.client.fetch_meetings(self.session, start, end)), quiet=True)

    def meetings_on(self, day):
        stamp = day.isoformat()
        return [m for m in self.meetings.get((self.cal_year, self.cal_month), ()) if m.day == stamp]
