"""Extra dashboard pages: Today, Calendar, Mentions, Team, Weekly summary, Settings.

Mixed into DashboardView, which owns the shared state (tasks, store, queue).
"""

import datetime
import os
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, ttk

from . import insights, richtext, settings, theme, updates, winshell
from .odoo_client import OdooConnectionError
from .theme import Card, PillButton

WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
ALL_PEOPLE = "Everyone"


def _bucket_color(bucket):
    return {
        "overdue": theme.DANGER,
        "today": theme.WARN,
        "week": theme.PURPLE,
        "later": theme.ACCENT,
        "none": theme.NEUTRAL,
        "done": theme.NEUTRAL,
    }[bucket]


class PagesMixin:
    # Shared -------------------------------------------------------------

    def _init_pages_state(self):
        today = datetime.date.today()
        self.cal_year, self.cal_month = today.year, today.month
        self.day_filter = None
        self.mentions = ()
        self._mentions_loaded = False
        self.project_list = ()
        self.team_tasks = ()
        self.team_project = ""
        self.closed_week = ()
        self._task_images = []
        self._replies_since = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def _background(self, kind, work):
        def run():
            try:
                self.results.put((kind, work()))
            except OdooConnectionError as exc:
                self.results.put(("side_error", str(exc)))
            except Exception:
                self.results.put(("side_error", "Could not load this from Odoo."))

        threading.Thread(target=run, daemon=True).start()

    def _on_show(self, key):
        """Load data a page needs the first time it is opened."""
        if key == "mentions" and not self._mentions_loaded:
            self.load_mentions()
        elif key == "team" and not self.project_list:
            self._background("projects", lambda: self.client.fetch_projects(self.session))
        elif key == "summary":
            self.load_summary()
        self._on_show_odoo(key)

    def _render_extra(self):
        self._render_today()
        self._render_calendar()
        self._render_summary()
        if self._mentions_loaded:
            self._render_mentions()
        if self.team_tasks:
            self._render_team()
        self._render_odoo()

    def nav_clicked(self, key):
        if key == "focus" and self.day_filter:
            self.day_filter = None
            self.render_focus()
        self.show_tab(key)

    def _handle_side_result(self, kind, payload):
        """Queue items produced by this mixin. Returns True when handled."""
        if kind == "mentions":
            self.mentions = payload
            self._mentions_loaded = True
            self._render_mentions()
        elif kind == "projects":
            self.project_list = payload
            self.team_box.configure(values=[name for _id, name in payload])
        elif kind == "team":
            name, tasks = payload
            if name == self.team_project:
                self.team_tasks = tasks
                self._render_team()
        elif kind == "closed":
            self.closed_week = payload
            self._render_summary()
        elif kind == "replies":
            self._notify_replies(payload)
        elif kind == "image":
            self._show_inline_image(*payload)
        elif kind == "update":
            self._update_result(*payload)
        elif kind == "side_error":
            self._set_status(payload, error=True)
        else:
            return self._handle_odoo_result(kind, payload)
        return True

    # New version notice -------------------------------------------------------

    def check_for_update(self, manual=False):
        """Ask GitHub for the latest release, in the background, at most once a day."""
        checker = self.update_checker or (updates.fetch_latest if manual else None)
        if checker is None or not (manual or settings.flag("updates")):
            return
        now = datetime.datetime.now()
        if not manual and self._update_checked and now - self._update_checked < datetime.timedelta(
            hours=updates.CHECK_EVERY_HOURS
        ):
            return
        self._update_checked = now
        if manual:
            self.update_status.set("Checking…")

        def run():
            try:
                latest = checker()
            except Exception:
                latest = ""
            self.results.put(("update", (latest, manual)))

        threading.Thread(target=run, daemon=True).start()

    def _update_result(self, latest, manual=False):
        if latest and updates.is_newer(latest):
            first_time = latest != self.new_version
            self.new_version = latest
            self._show_update_notice()
            if first_time and self.store.should_notify(self._today(), f"update:{latest}"):
                self._notify("Odoo Desktop update", f"Version {latest} is available. Click the notice in the app to download it.")
        elif manual:
            self.update_status.set(
                f"You have the latest version ({updates.VERSION})." if latest else "Could not reach GitHub. Try again later."
            )

    def _show_update_notice(self):
        if self.new_version:
            self.update_label.configure(text=f"Version {self.new_version} is available  " + chr(0x2192))
            self.update_label.pack(fill="x", pady=(0, 6), before=self.timer_label)
            if hasattr(self, "update_button") and self.update_button.winfo_exists():
                self.update_status.set(f"Version {self.new_version} is available.")
                self.update_button.configure(text=f"Download {self.new_version}")
        else:
            self.update_label.pack_forget()

    def open_download_page(self):
        webbrowser.open(updates.DOWNLOAD_PAGE)

    def _update_button_clicked(self):
        self.update_clicked()

    # Notifications ------------------------------------------------------------

    def _notify(self, title, text):
        if self.notifier and settings.flag("notify"):
            self.notifier(title, text)

    def _after_load(self, previous_ids):
        today = self._today()
        overdue = sum(1 for task in self.open_tasks if insights.bucket_of(task, today) == "overdue")
        if self.on_overdue:
            self.on_overdue(overdue, len(self.open_tasks))
        if previous_ids is not None:
            fresh = [task for task in self.open_tasks if task.id not in previous_ids]
            for task in fresh[:3]:
                self._notify("New task for you", f"{task.name}\n{task.project}")
        due = [
            task
            for task in self.open_tasks
            if insights.bucket_of(task, today) == "today" and self.store.should_notify(today, f"due:{task.id}")
        ]
        if due:
            names = ", ".join(task.name for task in due[:3]) + ("…" if len(due) > 3 else "")
            self._notify(f"{len(due)} task(s) due today", names)

    def _notify_replies(self, replies):
        self._replies_since = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        for reply in list(replies)[:3]:
            task = self._task(reply.task_id)
            where = task.name if task else "one of your tasks"
            self._notify(f"{reply.author} replied", f"{where}\n{richtext.html_to_text(reply.body)[:140]}")
        if replies and self._detail_id in {reply.task_id for reply in replies}:
            self.open_task(self._detail_id)

    # Today ------------------------------------------------------------------------

    def _build_today_page(self, page):
        self.today_sub = tk.StringVar(value="Pick the tasks you want to finish today.")
        head = self._page_header(page, "Today", self.today_sub)
        PillButton(head, "Plan with AI", self.ai_plan_day, kind="secondary", outer=theme.BG).pack(side="right")
        body = tk.Frame(page, bg=theme.BG)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        plan = Card(body, padding=(10, 12))
        plan.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        tk.Label(plan.body, text="MY PLAN", bg=theme.SURFACE, fg=theme.FAINT, font=(theme.FONT_BOLD, 8)).pack(
            anchor="w", padx=10, pady=(2, 6)
        )
        self.today_box = tk.Frame(plan.body, bg=theme.SURFACE)
        self.today_box.pack(fill="both", expand=True)
        side = Card(body, padding=(10, 12), fixed_width=340)
        side.grid(row=0, column=1, sticky="nsew")
        tk.Label(side.body, text="SUGGESTIONS", bg=theme.SURFACE, fg=theme.FAINT, font=(theme.FONT_BOLD, 8)).pack(
            anchor="w", padx=10, pady=(2, 6)
        )
        self.suggest_box = tk.Frame(side.body, bg=theme.SURFACE)
        self.suggest_box.pack(fill="both", expand=True)

    def today_ids(self):
        return self.store.today_plan(self._today(), {task.id for task in self.open_tasks})

    def today_suggestions(self):
        today = self._today()
        planned = set(self.today_ids())
        order = {"overdue": 0, "today": 1, "week": 2}
        pool = [
            task
            for task in self.open_tasks
            if task.id not in planned and (insights.bucket_of(task, today) in ("overdue", "today") or task.id in self.store.pins)
        ]
        pool.sort(key=lambda t: (order.get(insights.bucket_of(t, today), 3), t.deadline or "9999", t.name.lower()))
        return pool[:7]

    def _render_today(self):
        if not self._loaded:
            return
        today = self._today()
        for box in (self.today_box, self.suggest_box):
            for child in box.winfo_children():
                child.destroy()
        ids = self.today_ids()
        done = [i for i in ids if self.store.plan_is_done(today, i)]
        label = today.strftime("%A %d %B")
        if ids:
            self.today_sub.set(f"{label}  ·  {len(done)} of {len(ids)} done")
        else:
            self.today_sub.set(f"{label}  ·  Pick the tasks you want to finish today.")
        if not ids:
            tk.Label(
                self.today_box,
                text="Nothing planned yet. Add tasks from the suggestions,\nfrom Focus, or from a task page.",
                bg=theme.SURFACE, fg=theme.MUTED, font=(theme.FONT, 10), justify="left",
            ).pack(anchor="w", padx=10, pady=10)  # fmt: skip
        for task_id in ids:
            task = self._task(task_id)
            ticked = task_id in done
            row = tk.Frame(self.today_box, bg=theme.SURFACE)
            row.pack(fill="x")
            box = tk.Canvas(row, width=20, height=20, bg=theme.SURFACE, highlightthickness=0, cursor="hand2")
            if ticked:
                box.create_oval(2, 2, 18, 18, fill=theme.SUCCESS, outline=theme.SUCCESS)
                box.create_line(6, 10, 9, 13, 14, 7, fill=theme.SURFACE, width=2)
            else:
                box.create_oval(2, 2, 18, 18, outline=theme.FAINT, width=2)
            box.pack(side="left", padx=(10, 12), pady=10)
            box.bind("<Button-1>", lambda _e, i=task_id: self.tick_today(i))
            remove = tk.Label(row, text="✕", bg=theme.SURFACE, fg=theme.FAINT, font=(theme.FONT, 9), cursor="hand2")
            remove.pack(side="right", padx=12)
            remove.bind("<Button-1>", lambda _e, i=task_id: self.toggle_today(i))
            texts = tk.Frame(row, bg=theme.SURFACE, cursor="hand2")
            texts.pack(side="left", fill="x", expand=True)
            name = task.name if task else "Task no longer in your open list"
            title = tk.Label(
                texts, text=name, bg=theme.SURFACE, fg=theme.FAINT if ticked else theme.TEXT, anchor="w",
                font=(theme.FONT_BOLD, 10, "overstrike") if ticked else (theme.FONT_BOLD, 10),
            )  # fmt: skip
            title.pack(fill="x")
            if task:
                sub = tk.Label(
                    texts, text=f"{task.project}  ·  {task.stage}  ·  {insights.deadline_text(task, today)}",
                    bg=theme.SURFACE, fg=theme.MUTED, font=(theme.FONT, 9), anchor="w",
                )  # fmt: skip
                sub.pack(fill="x")
                for widget in (texts, title, sub):
                    widget.bind("<Button-1>", lambda _e, i=task_id: self.open_task(i))

        suggestions = self.today_suggestions()
        if not suggestions:
            tk.Label(
                self.suggest_box, text="No overdue, due-today or pinned\ntasks left to suggest.", bg=theme.SURFACE,
                fg=theme.MUTED, font=(theme.FONT, 10), justify="left",
            ).pack(anchor="w", padx=10, pady=10)  # fmt: skip
        for task in suggestions:
            bucket = insights.bucket_of(task, today)
            row = tk.Frame(self.suggest_box, bg=theme.SURFACE)
            row.pack(fill="x", pady=5)
            add = tk.Label(row, text="+ Add", bg=theme.SURFACE, fg=theme.ACCENT, font=(theme.FONT_BOLD, 9), cursor="hand2")
            add.pack(side="right", padx=10)
            add.bind("<Button-1>", lambda _e, i=task.id: self.toggle_today(i))
            texts = tk.Frame(row, bg=theme.SURFACE)
            texts.pack(side="left", fill="x", expand=True, padx=(10, 0))
            tk.Label(
                texts, text=task.name, bg=theme.SURFACE, fg=theme.TEXT, font=(theme.FONT, 10), anchor="w",
                wraplength=230, justify="left",
            ).pack(fill="x")  # fmt: skip
            tk.Label(
                texts, text=insights.deadline_text(task, today) if task.deadline else "Pinned",
                bg=theme.SURFACE, fg=_bucket_color(bucket) if bucket in ("overdue", "today") else theme.MUTED,
                font=(theme.FONT, 9), anchor="w",
            ).pack(fill="x")  # fmt: skip

    def tick_today(self, task_id):
        self.store.toggle_plan_done(self._today(), task_id)
        self._render_today()

    # Calendar ---------------------------------------------------------------------

    def _build_calendar_page(self, page):
        self.cal_title = tk.StringVar()
        head = self._page_header(page, "Calendar", self.cal_title)
        PillButton(head, "", lambda: self.shift_calendar(1), kind="secondary", outer=theme.BG, icon=theme.ICONS["next"], width=40).pack(
            side="right"
        )
        PillButton(head, "This month", lambda: self.shift_calendar(0), kind="secondary", outer=theme.BG).pack(
            side="right", padx=8
        )
        PillButton(head, "", lambda: self.shift_calendar(-1), kind="secondary", outer=theme.BG, icon=theme.ICONS["prev"], width=40).pack(
            side="right"
        )
        card = Card(page, padding=(8, 8))
        card.pack(fill="both", expand=True)
        grid = tk.Frame(card.body, bg=theme.BORDER)
        grid.pack(fill="both", expand=True)
        self.cal_cells = []
        for column, name in enumerate(WEEKDAYS):
            grid.columnconfigure(column, weight=1, uniform="day")
            tk.Label(grid, text=name, bg=theme.SURFACE, fg=theme.FAINT, font=(theme.FONT_BOLD, 8), pady=6).grid(
                row=0, column=column, sticky="nsew", padx=(0, 1), pady=(0, 1)
            )
        for row in range(6):
            grid.rowconfigure(row + 1, weight=1, uniform="week")
            for column in range(7):
                cell = tk.Frame(grid, bg=theme.SURFACE)
                cell.grid(row=row + 1, column=column, sticky="nsew", padx=(0, 1), pady=(0, 1))
                cell.pack_propagate(False)
                self.cal_cells.append(cell)

    def shift_calendar(self, delta):
        if delta == 0:
            today = self._today()
            self.cal_year, self.cal_month = today.year, today.month
        else:
            self.cal_year, self.cal_month = insights.shift_month(self.cal_year, self.cal_month, delta)
        self._render_calendar()
        self._load_meetings()

    def _render_calendar(self):
        today = self._today()
        first = datetime.date(self.cal_year, self.cal_month, 1)
        by_day = insights.tasks_by_day(self.open_tasks)
        in_month = sum(len(v) for k, v in by_day.items() if k[:7] == first.isoformat()[:7])
        self.cal_title.set(f"{first.strftime('%B %Y')}  ·  {in_month} deadline(s) this month")
        days = [day for week in insights.month_grid(self.cal_year, self.cal_month) for day in week]
        for cell, day in zip(self.cal_cells, days):
            for child in cell.winfo_children():
                child.destroy()
            other = day.month != self.cal_month
            bg = theme.SURFACE_2 if day == today else theme.SURFACE
            cell.configure(bg=bg)
            tk.Label(
                cell, text=str(day.day), bg=bg, anchor="w",
                fg=theme.ACCENT if day == today else (theme.FAINT if other else theme.TEXT),
                font=(theme.FONT_BOLD if day == today else theme.FONT, 9),
            ).pack(fill="x", padx=8, pady=(5, 1))  # fmt: skip
            tasks = by_day.get(day.isoformat(), [])
            meetings = self.meetings_on(day)
            room = 3 if len(tasks) + len(meetings) <= 3 else 2
            listed = meetings[:room]
            for meeting in listed:
                label = tk.Label(
                    cell, text=(meeting.time + "  " if meeting.time else "") + meeting.name, bg=bg, fg=theme.PURPLE,
                    font=(theme.FONT, 8), anchor="w", cursor="hand2",
                )  # fmt: skip
                label.pack(fill="x", padx=8)
                label.bind("<Button-1>", lambda _e, i=meeting.id: self.open_record("calendar.event", i))
            shown = tasks[: room - len(listed)]
            for task in shown:
                label = tk.Label(
                    cell, text=task.name, bg=bg, fg=_bucket_color(insights.bucket_of(task, today)) if day <= today else theme.MUTED,
                    font=(theme.FONT, 8), anchor="w", cursor="hand2",
                )  # fmt: skip
                label.pack(fill="x", padx=8)
                label.bind("<Button-1>", lambda _e, i=task.id: self.open_task(i))
            hidden = len(tasks) + len(meetings) - len(shown) - len(listed)
            if hidden > 0:
                more = tk.Label(
                    cell, text=f"+{hidden} more", bg=bg, fg=theme.ACCENT, font=(theme.FONT, 8), anchor="w", cursor="hand2"
                )
                more.pack(fill="x", padx=8)
                more.bind("<Button-1>", lambda _e, d=day.isoformat(): self.show_day(d))

    def show_day(self, day):
        self._reset_filters()
        self.day_filter = day
        self.render_focus()
        self.show_tab("focus")

    def clear_day_filter(self):
        self.day_filter = None
        self.render_focus()

    # Mentions --------------------------------------------------------------------

    def _build_mentions_page(self, page):
        self.mentions_sub = tk.StringVar(value="Messages that tagged you. Tick them off when you have dealt with them.")
        self._page_header(
            page, "Mentions", self.mentions_sub, action=("Refresh", self.load_mentions, "secondary", "refresh")
        )
        card = Card(page, padding=(16, 10))
        card.pack(fill="both", expand=True)
        tools = ttk.Frame(card.body)
        tools.pack(fill="x", pady=(2, 8))
        self.unchecked_only = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            tools, text="Hide checked messages", variable=self.unchecked_only, command=self._render_mentions
        ).pack(side="left")
        PillButton(tools, "Check all", self.check_all_mentions, kind="secondary", height=32).pack(side="right")
        self.check_button = PillButton(tools, "Mark checked", self.toggle_checked_mentions, kind="primary", height=32)
        self.check_button.pack(side="right", padx=(0, 8))
        box = ttk.Frame(card.body)
        box.pack(fill="both", expand=True)
        self.mentions_tree = ttk.Treeview(
            box, columns=("status", "who", "where", "when", "text"), show="headings", selectmode="extended"
        )
        for name, text, width, stretch in (
            ("status", "STATUS", 150, False),
            ("who", "FROM", 130, False),
            ("where", "ON", 220, True),
            ("when", "WHEN", 125, False),
            ("text", "MESSAGE", 300, True),
        ):
            self.mentions_tree.heading(name, text=text, anchor="w")
            self.mentions_tree.column(name, width=width, anchor="w", stretch=stretch)
        self.mentions_tree.tag_configure("waiting", foreground=theme.WARN)
        self.mentions_tree.tag_configure("checked", foreground=theme.FAINT)
        self.mentions_tree.pack(side="left", fill="both", expand=True)
        theme.attach_scrollbar(self.mentions_tree, box)
        self.mentions_tree.bind("<Double-1>", self._mention_double_click)
        self.mentions_tree.bind("<Button-1>", self._mention_click, add="+")
        self.mentions_tree.bind("<<TreeviewSelect>>", lambda _e: self._update_check_button(), add="+")
        self.mentions_tree.bind("<space>", lambda _e: self.toggle_checked_mentions())

    def load_mentions(self):
        self.mentions_sub.set("Loading mentions…")
        self._background("mentions", lambda: self.client.fetch_mentions(self.session))

    def mention_checked(self, mention):
        return self.store.mention_checked(mention.id)

    def visible_mentions(self):
        return [m for m in self.mentions if not (self.unchecked_only.get() and self.mention_checked(m))]

    def _mention_status(self, mention):
        if self.mention_checked(mention):
            return chr(0x2713) + " Checked"
        return "To check" + (" (replied)" if mention.answered else "")

    def _render_mentions(self):
        tree = self.mentions_tree
        selected = set(tree.selection())
        tree.delete(*tree.get_children())
        waiting = sum(1 for m in self.mentions if not self.mention_checked(m))
        self.tab_text["mentions"].set(f"Mentions ({waiting})" if waiting else "Mentions")
        self.mentions_sub.set(
            f"{waiting} message(s) to check. Click the status to tick one off, double-click to open."
            if waiting
            else "You have checked every message that tagged you."
        )
        for mention in self.visible_mentions():
            text = " ".join(richtext.html_to_text(mention.body).split())
            tree.insert(
                "",
                "end",
                iid=str(mention.id),
                values=(self._mention_status(mention), mention.author, mention.record, mention.date, text[:160]),
                tags=("checked" if self.mention_checked(mention) else "waiting",),
            )
        tree.selection_set([iid for iid in selected if tree.exists(iid)])
        self._update_check_button()

    def _selected_mentions(self):
        selection = set(self.mentions_tree.selection())
        return [m for m in self.mentions if str(m.id) in selection]

    def _update_check_button(self):
        chosen = self._selected_mentions()
        undo = bool(chosen) and all(self.mention_checked(m) for m in chosen)
        self.check_button.configure(text="Uncheck" if undo else "Mark checked")

    def toggle_checked_mentions(self):
        """Tick the selected messages; if they are all ticked already, untick them."""
        chosen = self._selected_mentions()
        if not chosen:
            self.mentions_sub.set("Select one or more messages first.")
            return
        undo = all(self.mention_checked(m) for m in chosen)
        self.store.set_mentions_checked([m.id for m in chosen], not undo)
        self._render_mentions()

    def check_all_mentions(self):
        todo = [m for m in self.mentions if not self.mention_checked(m)]
        if not todo or not self._confirm("Check all", f"Mark {len(todo)} message(s) as checked?"):
            return
        self.store.set_mentions_checked([m.id for m in todo], True)
        self._render_mentions()

    def _mention_click(self, event):
        """A click in the STATUS column ticks or unticks that message."""
        tree = self.mentions_tree
        row = tree.identify_row(event.y)
        if not row or tree.identify_column(event.x) != "#1":
            return None
        mention = next((m for m in self.mentions if str(m.id) == row), None)
        if mention is None:
            return None
        self.store.set_mentions_checked([mention.id], not self.mention_checked(mention))
        self._render_mentions()
        return "break"

    def _mention_double_click(self, event):
        if self.mentions_tree.identify_column(event.x) == "#1":
            return "break"
        self._open_mention()
        return None

    def _open_mention(self):
        selection = self.mentions_tree.selection()
        mention = next((m for m in self.mentions if selection and str(m.id) == selection[0]), None)
        if mention is None:
            return
        if mention.model == "project.task":
            self.open_task(mention.res_id)
        else:
            webbrowser.open(f"{self.session.base_url}/web#id={mention.res_id}&model={mention.model}&view_type=form")

    # Team -------------------------------------------------------------------------

    def _build_team_page(self, page):
        self.team_sub = tk.StringVar(value="Choose a project to see everyone's open tasks.")
        self._page_header(page, "Team", self.team_sub)
        card = Card(page, padding=(16, 12))
        card.pack(fill="both", expand=True)
        tools = ttk.Frame(card.body)
        tools.pack(fill="x", pady=(0, 10))
        self.team_var = tk.StringVar(value=self.team_project)
        self.team_box = ttk.Combobox(
            tools, textvariable=self.team_var, values=[name for _id, name in self.project_list], state="readonly",
            width=38, font=(theme.FONT, 10),
        )  # fmt: skip
        self.team_box.pack(side="left", ipady=3)
        self.team_box.bind("<<ComboboxSelected>>", lambda _e: self.load_team(self.team_var.get()))
        tk.Frame(card.body, bg=theme.BORDER, height=1).pack(fill="x")
        box = ttk.Frame(card.body)
        box.pack(fill="both", expand=True)
        self.team_tree = ttk.Treeview(box, columns=("stage", "deadline", "idle"), show="tree headings")
        self.team_tree.heading("#0", text="PERSON / TASK", anchor="w")
        self.team_tree.column("#0", width=380, anchor="w")
        for name, text, width in (("stage", "STAGE", 150), ("deadline", "DEADLINE", 110), ("idle", "UNTOUCHED", 100)):
            self.team_tree.heading(name, text=text, anchor="w")
            self.team_tree.column(name, width=width, anchor="w", stretch=name == "stage")
        self.team_tree.tag_configure("group", font=(theme.FONT_BOLD, 9), foreground=theme.TEXT, background=theme.SURFACE_2)
        self.team_tree.tag_configure("overdue", foreground=theme.DANGER)
        self.team_tree.tag_configure("today", foreground=theme.WARN)
        self.team_tree.pack(side="left", fill="both", expand=True)
        theme.attach_scrollbar(self.team_tree, box)
        self.team_tree.bind("<Double-1>", lambda _e: self._open_team_task())

    def load_team(self, project_name):
        project_id = next((pid for pid, name in self.project_list if name == project_name), None)
        if project_id is None:
            return
        self.team_project = project_name
        self.team_sub.set(f"Loading {project_name}…")
        self._background("team", lambda: (project_name, self.client.fetch_team_tasks(self.session, project_id)))

    def team_groups(self):
        """[(person, [tasks])] with the most overdue people first."""
        today = self._today()
        groups = {}
        for item in self.team_tasks:
            for person in item.assignees or ("Unassigned",):
                groups.setdefault(person, []).append(item.task)

        def overdue(tasks):
            return sum(1 for task in tasks if insights.bucket_of(task, today) == "overdue")

        return sorted(groups.items(), key=lambda pair: (-overdue(pair[1]), -len(pair[1]), pair[0].lower()))

    def _render_team(self):
        today = self._today()
        tree = self.team_tree
        tree.delete(*tree.get_children())
        groups = self.team_groups()
        total = len(self.team_tasks)
        late = sum(1 for item in self.team_tasks if insights.bucket_of(item.task, today) == "overdue")
        self.team_sub.set(
            f"{self.team_project}  ·  {total} open task(s), {late} overdue, {len(groups)} people. Double-click a task to read it."
        )
        order = [key for key, _label in insights.BUCKETS]
        for index, (person, tasks) in enumerate(groups):
            overdue = sum(1 for task in tasks if insights.bucket_of(task, today) == "overdue")
            stale = sum(1 for task in tasks if insights.is_stale(task, today))
            summary = f"{person}   ·   {len(tasks)} open"
            if overdue:
                summary += f"   ·   {overdue} overdue"
            if stale:
                summary += f"   ·   {stale} untouched"
            parent = tree.insert("", "end", iid=f"p:{index}", text=summary, open=index < 6, tags=("group",))
            tasks.sort(key=lambda t: (order.index(insights.bucket_of(t, today)), t.deadline or "9999", t.name.lower()))
            for task in tasks:
                bucket = insights.bucket_of(task, today)
                tree.insert(
                    parent,
                    "end",
                    iid=f"{index}:{task.id}",
                    text=task.name,
                    values=(task.stage, insights.deadline_text(task, today), insights.idle_text(task, today)),
                    tags=(bucket,) if bucket in ("overdue", "today") else (),
                )

    def _open_team_task(self):
        selection = self.team_tree.selection()
        if selection and not selection[0].startswith("p:"):
            self.open_task(int(selection[0].split(":", 1)[1]))

    # Weekly summary ----------------------------------------------------------------

    def _build_summary_page(self, page):
        self.summary_sub = tk.StringVar(value="What happened to your tasks this week.")
        head = self._page_header(page, "Weekly summary", self.summary_sub)
        PillButton(head, "Save", self.save_summary, kind="secondary", outer=theme.BG, icon=theme.ICONS["export"]).pack(
            side="right"
        )
        PillButton(head, "Copy", self.copy_summary, kind="primary", outer=theme.BG).pack(side="right", padx=(0, 8))
        PillButton(head, "Write with AI", self.ai_weekly_report, kind="secondary", outer=theme.BG).pack(
            side="right", padx=(0, 8)
        )
        card = Card(page, padding=(6, 6))
        card.pack(fill="both", expand=True)
        box = ttk.Frame(card.body)
        box.pack(fill="both", expand=True)
        text = tk.Text(
            box, wrap="word", relief="flat", bd=0, highlightthickness=0, width=20, height=5, font=(theme.FONT, 10),
            fg=theme.TEXT, bg=theme.SURFACE, padx=18, pady=12, spacing3=6, cursor="arrow", state="disabled",
        )  # fmt: skip
        text.pack(side="left", fill="both", expand=True)
        theme.attach_scrollbar(text, box)
        text.tag_configure("section", font=(theme.FONT_BOLD, 8), foreground=theme.FAINT, spacing1=16, spacing3=8)
        text.tag_configure("item", lmargin1=14, lmargin2=30)
        text.tag_configure("muted", foreground=theme.MUTED, lmargin1=14)
        self.summary_text_widget = text

    def load_summary(self):
        monday, _sunday = insights.week_range(self._today())
        self._background("closed", lambda: self.client.fetch_closed_since(self.session, monday.isoformat()))

    def summary_sections(self):
        return insights.week_summary(self._today(), self.store.history, self.closed_week, self.open_tasks)

    def summary_text(self):
        span, sections = self.summary_sections()
        lines = [f"Weekly summary, {span}", ""]
        for title, items in sections:
            lines.append(f"{title} ({len(items)})")
            lines.extend(f"  - {item}" for item in items)
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _render_summary(self):
        if not self._loaded:
            return
        span, sections = self.summary_sections()
        self.summary_sub.set(f"Week of {span}. Built from Odoo and from what this app saw since Monday.")
        text = self.summary_text_widget
        text.configure(state="normal")
        text.delete("1.0", "end")
        for title, items in sections:
            text.insert("end", f"{title.upper()}   {len(items)}\n", ("section",))
            if not items:
                text.insert("end", "Nothing.\n", ("muted",))
            for item in items:
                text.insert("end", f"•  {item}\n", ("item",))
        text.configure(state="disabled")

    def copy_summary(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.summary_text())
        self._set_status("Weekly summary copied. Paste it anywhere.")

    def save_summary(self):
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save weekly summary",
            defaultextension=".txt",
            initialfile=f"weekly-summary-{self._today().isoformat()}.txt",
            filetypes=[("Text file", "*.txt")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self.summary_text())
        except OSError:
            self._set_status("Could not save the file.", error=True)
            return
        self._set_status(f"Saved to {path}")

    # Settings ----------------------------------------------------------------------

    def credential_target(self):
        return winshell.credential_target(self.session.base_url, self.session.database, self.email)

    def _build_settings_page(self, page):
        self._page_header(page, "Settings", "Preferences are saved on this computer.")
        card = Card(page, padding=(22, 18))
        card.pack(fill="both", expand=True)
        scroll = theme.Scrollable(card.body, theme.SURFACE)
        scroll.pack(fill="both", expand=True)
        body = scroll.inner

        def section(text):
            tk.Label(body, text=text, bg=theme.SURFACE, fg=theme.FAINT, font=(theme.FONT_BOLD, 8)).pack(
                anchor="w", pady=(14, 6)
            )

        def toggle(name, label, hint, command=None):
            variable = tk.BooleanVar(value=settings.flag(name))

            def changed():
                settings.set_flag(name, variable.get())
                if command:
                    command(variable.get())

            ttk.Checkbutton(body, text=label, variable=variable, command=changed).pack(anchor="w")
            tk.Label(body, text=hint, bg=theme.SURFACE, fg=theme.FAINT, font=(theme.FONT, 9), justify="left").pack(
                anchor="w", padx=(24, 0), pady=(0, 8)
            )
            return variable

        section("NOTIFICATIONS")
        self.notify_flag = toggle(
            "notify",
            "Show Windows notifications",
            "When a task is assigned to you, a deadline is today, or someone replies on one of your tasks.",
        )
        self.tray_flag = toggle(
            "tray",
            "Keep running next to the clock when I close the window",
            "The app keeps refreshing in the background. Right-click its icon to quit.",
        )
        toggle(
            "hotkey",
            "Quick capture: Ctrl+Alt+N creates a task from anywhere",
            "Works while the app runs, even hidden next to the clock. Applies the next time the app starts.",
        )
        self.autostart_flag = tk.BooleanVar(value=winshell.autostart_enabled())
        ttk.Checkbutton(
            body, text="Start with Windows, hidden next to the clock", variable=self.autostart_flag,
            command=self._autostart_changed,
        ).pack(anchor="w")  # fmt: skip
        tk.Label(
            body, text="Needs Stay signed in, otherwise the sign-in window opens at start.", bg=theme.SURFACE,
            fg=theme.FAINT, font=(theme.FONT, 9), justify="left",
        ).pack(anchor="w", padx=(24, 0), pady=(0, 8))  # fmt: skip
        section("SIGN-IN")
        self.stay_flag = toggle(
            "stay",
            "Stay signed in on this computer",
            "Your password or API key is kept in Windows Credential Manager, not in a file. Untick to forget it.",
            command=self._stay_changed,
        )
        self._build_ai_settings(body, section)
        section("UPDATES")
        toggle(
            "updates",
            "Tell me when a new version is available",
            "Once a day the app asks GitHub for the latest release. Nothing about you or your tasks is sent.",
        )
        row = tk.Frame(body, bg=theme.SURFACE)
        row.pack(anchor="w")
        self.update_button = PillButton(row, "Check now", self._update_button_clicked, kind="secondary")
        self.update_button.pack(side="left")
        self.update_status = tk.StringVar(value=f"You have version {updates.VERSION}.")
        tk.Label(row, textvariable=self.update_status, bg=theme.SURFACE, fg=theme.MUTED, font=(theme.FONT, 9)).pack(
            side="left", padx=(12, 0)
        )
        if self.new_version:
            self._show_update_notice()
        section("APPEARANCE")
        PillButton(
            body, "Switch to light theme" if theme.MODE == "dark" else "Switch to dark theme", self.toggle_theme,
            kind="secondary", icon=theme.ICONS["sun" if theme.MODE == "dark" else "moon"],
        ).pack(anchor="w")  # fmt: skip
        section("DATA ON THIS COMPUTER")
        tk.Label(
            body, text=f"Pins, notes, plans and history:  {settings.data_dir()}", bg=theme.SURFACE, fg=theme.MUTED,
            font=(theme.FONT, 9),
        ).pack(anchor="w", pady=(0, 8))  # fmt: skip
        PillButton(body, "Open folder", self._open_data_folder, kind="secondary").pack(anchor="w")

    def _autostart_changed(self):
        wanted = self.autostart_flag.get()
        if winshell.set_autostart(wanted):
            self._set_status("Odoo Desktop will start with Windows." if wanted else "Odoo Desktop no longer starts with Windows.")
        else:
            self.autostart_flag.set(winshell.autostart_enabled())
            self._set_status("Windows did not let the app change this.", error=True)

    def _stay_changed(self, enabled):
        target = self.credential_target()
        if enabled:
            if winshell.save_password(target, self.email, self.session.api_key):
                self._set_status("You will stay signed in on this computer.")
            else:
                settings.set_flag("stay", False)
                self.stay_flag.set(False)
                self._set_status("Windows could not save the password.", error=True)
        else:
            winshell.delete_password(target)
            self._set_status("Saved password removed.")

    def _open_data_folder(self):
        folder = settings.data_dir()
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))  # noqa: S606
