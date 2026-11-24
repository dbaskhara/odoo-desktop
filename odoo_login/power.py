"""Features added in 6.0 on top of the pages: "Today in Odoo" tiles on the Overview,
search (Ctrl+K) and keyboard shortcuts, editing a task, the actions of
"More from Odoo", offline mode and the one-click update.

Mixed into DashboardView.
"""

import datetime
import sys
import threading
import tkinter as tk
from tkinter import ttk

from . import insights, richtext, theme, updates
from .theme import Card, PillButton

SEARCH_LIMIT = 40


def unique_labels(pairs):
    """[(id, name)] -> {label: id}, adding the id to names used twice."""
    seen, labels = {}, {}
    for _id, name in pairs:
        seen[name] = seen.get(name, 0) + 1
    for item_id, name in pairs:
        labels[name if seen[name] == 1 else f"{name}  (#{item_id})"] = item_id
    return labels


class PowerMixin:
    def _init_power_state(self):
        self.today_meetings = None  # None: not loaded, or not available
        self.week_lines = None
        self._unread_seen = None  # conversation id -> unread count at the last check
        self.offline = False
        self._search_window = None
        self._search_text = ""
        self._search_remote = ()
        self._search_items = []

    # "Today in Odoo" on the Overview ------------------------------------------------

    def _build_odoo_strip(self, page):
        ttk.Label(page, text="TODAY IN ODOO", style="Section.TLabel").pack(anchor="w", pady=(22, 8))
        row = tk.Frame(page, bg=theme.BG)
        row.pack(fill="x")
        self.odoo_vars = {}
        tiles = (
            ("activities", "Activities to do", "activities"),
            ("discuss", "Unread messages", "discuss"),
            ("meetings", "Meetings today", "calendar_page"),
            ("hours", "Hours logged today", "timesheets"),
        )
        for index, (key, title, icon) in enumerate(tiles):
            row.columnconfigure(index, weight=1, uniform="odoo")
            card = Card(row, padding=(14, 12))
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 12, 0))
            tile = tk.Canvas(card.body, width=38, height=38, bg=theme.SURFACE, highlightthickness=0)
            theme.round_rect(tile, 0, 0, 38, 38, 10, fill=theme.ACCENT_SOFT, outline=theme.ACCENT_SOFT)
            tile.create_text(19, 20, text=theme.ICONS[icon], fill=theme.ACCENT, font=(theme.ICON_FONT, 12))
            tile.pack(side="left")
            texts = tk.Frame(card.body, bg=theme.SURFACE)
            texts.pack(side="left", padx=(12, 0))
            value, sub = tk.StringVar(value="—"), tk.StringVar(value=title)
            tk.Label(texts, textvariable=value, bg=theme.SURFACE, fg=theme.TEXT, font=(theme.FONT_BOLD, 13)).pack(anchor="w")
            tk.Label(texts, textvariable=sub, bg=theme.SURFACE, fg=theme.MUTED, font=(theme.FONT, 9)).pack(anchor="w")
            self.odoo_vars[key] = (value, sub, title)
            target = "calendar" if key == "meetings" else ("timesheets" if key == "hours" else key)
            card.make_clickable(lambda t=target: self.show_tab(t))
        self._render_odoo_strip()

    def odoo_strip(self):
        """{tile: (big text, small text)} from what is loaded; '—' when unknown."""
        result = {}
        if self._activities_loaded:
            late = sum(1 for row in self.activities if row.tag == "overdue")
            now = sum(1 for row in self.activities if row.tag == "today")
            result["activities"] = (f"{late + now} to do", f"{late} late, {now} due today" if late + now else "Nothing due today")
        if self._conversations_loaded:
            unread = sum(c.unread for c in self.conversations)
            talks = sum(1 for c in self.conversations if c.unread)
            result["discuss"] = (f"{unread} unread", f"in {talks} conversation(s)" if unread else "No new message")
        if self.today_meetings is not None:
            now = datetime.datetime.now().strftime("%H:%M")
            coming = next((m for m in self.today_meetings if m.time and m.time >= now), None)
            text = f"next {coming.time}  {coming.name}"[:34] if coming else "No more meeting today"
            count = len(self.today_meetings)
            result["meetings"] = (f"{count} meeting" + ("" if count == 1 else "s"), text if count else "None today")
        if self.week_lines is not None:
            today = self._today().isoformat()
            day = sum(line.hours for line in self.week_lines if line.date == today)
            week = sum(line.hours for line in self.week_lines)
            result["hours"] = (f"{_hours(day) or '0:00'} today", f"{_hours(week) or '0:00'} logged this week")
        return result

    def _render_odoo_strip(self):
        if not hasattr(self, "odoo_vars"):
            return
        values = self.odoo_strip()
        for key, (value, sub, title) in self.odoo_vars.items():
            big, small = values.get(key, ("—", title))
            value.set(big)
            sub.set(small)

    def _load_today_extras(self):
        client, session, today = self.client, self.session, self._today()
        if self.has_model("calendar.event") and hasattr(client, "fetch_meetings"):
            day = today.isoformat()
            self._odoo_load("today_meetings", lambda: client.fetch_meetings(session, day, day), quiet=True)
        if self.has_model("account.analytic.line") and hasattr(client, "fetch_timesheets"):
            monday, sunday = insights.week_range(today)
            self._odoo_load(
                "week_hours", lambda: client.fetch_timesheets(session, monday.isoformat(), sunday.isoformat()), quiet=True
            )

    def _handle_power_result(self, kind, payload):
        if kind == "today_meetings":
            self.today_meetings = payload
        elif kind == "week_hours":
            self.week_lines = payload
        elif kind == "search":
            text, rows = payload
            if text == self._search_text:
                self._search_remote = rows
                self._fill_search()
            return True
        elif kind == "channel_read":
            self._channel_marked_read(payload)
            return True
        elif kind == "update_ready":
            self._install_update(payload)
            return True
        else:
            return self._handle_ai_result(kind, payload)
        self._render_odoo_strip()
        return True

    # Discuss: notifications and mark as read ------------------------------------------

    def _notify_new_messages(self):
        """A Windows notification when a direct or group conversation gets new messages."""
        current = {c.id: c.unread for c in self.conversations}
        before, self._unread_seen = self._unread_seen, current
        if before is None:
            return
        for conversation in self.conversations:
            if conversation.kind == "channel" or conversation.id == self.channel_id and self.current_tab == "discuss":
                continue
            if conversation.unread > before.get(conversation.id, 0):
                self._notify(
                    f"Message from {conversation.name}",
                    f"{conversation.unread} unread message(s) in Odoo Discuss.",
                )

    def _mark_channel_read(self, channel_id):
        if not hasattr(self.client, "mark_channel_read"):
            return
        self._odoo_load(
            "channel_read",
            lambda: channel_id if self.client.mark_channel_read(self.session, channel_id) else None,
            quiet=True,
        )

    def _channel_marked_read(self, channel_id):
        if channel_id is None:
            return
        self.conversations = tuple(
            type(c)(c.id, c.name, c.kind, 0) if c.id == channel_id else c for c in self.conversations
        )
        if self._unread_seen is not None:
            self._unread_seen[channel_id] = 0
        self._render_conversations()

    # Search and keyboard shortcuts -----------------------------------------------------

    def _bind_shortcuts(self):
        root = self.root
        root.unbind_class("Text", "<Control-k>")  # Tk's "delete to end of line" would fire first
        root.bind_all("<Control-k>", lambda _e: self._shortcut(self.open_search))
        root.bind_all("<Control-n>", lambda _e: self._shortcut(self.new_task_clicked))
        root.bind_all("<F5>", lambda _e: self._shortcut(self.refresh))
        root.bind_all("<Escape>", lambda _e: self._shortcut(self._escape), add="+")
        for number in range(1, 10):
            root.bind_all(f"<Control-Key-{number}>", lambda _e, n=number: self._shortcut(lambda: self._go_to_page(n)))

    def _shortcut(self, action):
        if self._alive and not self.root.grab_current():
            action()
            return "break"
        return None

    def _escape(self):
        if self.current_tab == "task":
            self.close_task()

    def visible_pages(self):
        return [key for key in self.nav_rows if self.nav_rows[key][0].master.winfo_manager()]

    def _go_to_page(self, number):
        pages = self.visible_pages()
        if number <= len(pages):
            self.nav_clicked(pages[number - 1])

    def search_results(self, text):
        """[(label, kind, target)] found in what the app has loaded. All words must match."""
        words = text.lower().split()
        if not words:
            return []

        def hit(*parts):
            haystack = " ".join(str(part) for part in parts).lower()
            return all(word in haystack for word in words)

        found = []
        for key in self.visible_pages():
            label = self.tab_text[key].get()
            if hit(label):
                found.append((f"Go to  {label}", "page", key))
        for task in self.tasks:
            if hit(task.name, task.project, task.stage, f"#{task.id}"):
                found.append((f"Task  {task.name}   ·   {task.project}", "task", task.id))
        for row in self.activities:
            if hit(*row.cells):
                found.append((f"Activity  {row.cells[2] or row.cells[1]}   ·   {row.cells[3]}", "record", (row.model, row.res_id)))
        for conversation in self.conversations:
            if hit(conversation.name):
                found.append((f"Conversation  {conversation.name}", "conversation", conversation.id))
        for mention in self.mentions:
            if hit(mention.record, mention.author, richtext.html_to_text(mention.body)):
                found.append((f"Mention  {mention.author} on {mention.record}", "record", (mention.model, mention.res_id)))
        for key, rows in self.more_rows.items():
            for row in rows:
                if row.model and hit(*row.cells):
                    found.append((f"{_more_label(key)}  {row.cells[0]}   ·   {row.cells[1]}", "record", (row.model, row.res_id)))
        known = {target for _label, kind, target in found if kind == "task"}
        for task_id, name, project in self._search_remote:
            if task_id not in known:
                found.append((f"Task in Odoo  {name}   ·   {project}", "task", task_id))
        return found[:SEARCH_LIMIT]

    def open_search(self):
        if self._search_window is not None and self._search_window.winfo_exists():
            self._search_window.lift()
            return
        window = self._search_window = tk.Toplevel(self.root)
        window.title("Search")
        window.configure(bg=theme.SURFACE)
        window.transient(self.root)
        window.resizable(False, False)
        theme.set_title_bar(window)
        body = tk.Frame(window, bg=theme.SURFACE)
        body.pack(fill="both", expand=True, padx=16, pady=14)
        self.search_entry_var = tk.StringVar()
        entry = ttk.Entry(body, textvariable=self.search_entry_var, width=64, font=(theme.FONT, 11))
        entry.pack(fill="x", ipady=4)
        self.search_list = tk.Listbox(
            body, height=14, activestyle="none", relief="flat", highlightthickness=0, bg=theme.SURFACE,
            fg=theme.TEXT, selectbackground=theme.ACCENT_SOFT, selectforeground=theme.TEXT, font=(theme.FONT, 10),
            exportselection=False,
        )  # fmt: skip
        self.search_list.pack(fill="both", expand=True, pady=(10, 6))
        self.search_hint = tk.StringVar(value="Type to search tasks, activities, conversations, mentions and pages.")
        tk.Label(body, textvariable=self.search_hint, bg=theme.SURFACE, fg=theme.FAINT, font=(theme.FONT, 9)).pack(anchor="w")
        self._search_text, self._search_remote = "", ()
        self.search_entry_var.trace_add("write", lambda *_: self._search_changed())
        entry.bind("<Down>", lambda _e: self._search_move(1))
        entry.bind("<Up>", lambda _e: self._search_move(-1))
        entry.bind("<Return>", lambda _e: self._search_open())
        self.search_list.bind("<Double-1>", lambda _e: self._search_open())
        window.bind("<Escape>", lambda _e: window.destroy())
        window.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - window.winfo_width()) // 2
        y = self.root.winfo_rooty() + 110
        window.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        entry.focus_set()

    def _search_changed(self):
        text = self.search_entry_var.get().strip()
        self._search_text, self._search_remote = text, ()
        self._fill_search()
        if len(text) >= 3 and hasattr(self.client, "search_tasks"):
            self.root.after(450, lambda: self._search_odoo(text))

    def _search_odoo(self, text):
        if text == self._search_text and self._alive:
            self._odoo_load("search", lambda: (text, self.client.search_tasks(self.session, text)), quiet=True)

    def _fill_search(self):
        if self._search_window is None or not self._search_window.winfo_exists():
            return
        self._search_items = self.search_results(self._search_text)
        box = self.search_list
        box.delete(0, "end")
        for label, _kind, _target in self._search_items:
            box.insert("end", "  " + label)
        if self._search_items:
            box.selection_set(0)
        self.search_hint.set(
            f"{len(self._search_items)} result(s). Enter opens the selected one."
            if self._search_text
            else "Type to search tasks, activities, conversations, mentions and pages."
        )

    def _search_move(self, step):
        box = self.search_list
        if not self._search_items:
            return "break"
        current = box.curselection()
        index = min(max((current[0] if current else -1) + step, 0), len(self._search_items) - 1)
        box.selection_clear(0, "end")
        box.selection_set(index)
        box.see(index)
        return "break"

    def _search_open(self):
        current = self.search_list.curselection()
        if not current or current[0] >= len(self._search_items):
            return
        _label, kind, target = self._search_items[current[0]]
        self._search_window.destroy()
        self.open_search_result(kind, target)

    def open_search_result(self, kind, target):
        if kind == "page":
            self.nav_clicked(target)
        elif kind == "task":
            self.open_task(target)
        elif kind == "conversation":
            self.show_tab("discuss")
            if self.conversations_tree.exists(str(target)):
                self.conversations_tree.selection_set(str(target))
        else:
            self.open_record(*target)

    # Editing a task -------------------------------------------------------------------------

    def edit_task_clicked(self):
        if not self.detail:
            return
        ok, data = self._busy(lambda: self.client.fetch_task_edit_data(self.session, self._detail_id))
        if not ok:
            return
        users, tags = unique_labels(data["users"]), unique_labels(data["tags"])
        chosen_users = [label for label, uid in users.items() if uid in data["user_ids"]]
        chosen_tags = [label for label, tid in tags.items() if tid in data["tag_ids"]]
        fields = [
            ("deadline", "Deadline (YYYY-MM-DD, empty for none)", "entry", data["deadline"], None),
            ("priority", "Priority", "combo", "High" if data["starred"] else "Normal", ["Normal", "High"]),
            ("users", "Assigned to (click to select or unselect)", "multi", chosen_users, list(users)),
        ]
        if tags:
            fields.append(("tags", "Tags", "multi", chosen_tags, list(tags)))
        answer = self._form(f"Edit “{self.detail.name[:40]}”", fields, ok_text="Save in Odoo")
        if answer is None:
            return
        changes = {}
        if answer["deadline"] != data["deadline"]:
            changes["deadline"] = answer["deadline"]
        if (answer["priority"] == "High") != data["starred"]:
            changes["starred"] = answer["priority"] == "High"
        new_users = sorted(users[label] for label in answer["users"])
        if new_users != sorted(data["user_ids"]):
            changes["user_ids"] = new_users
        if tags:
            new_tags = sorted(tags[label] for label in answer["tags"])
            if new_tags != sorted(data["tag_ids"]):
                changes["tag_ids"] = new_tags
        if changes:
            self._busy(lambda: self.do_edit_task(**changes))
        else:
            self._set_status("Nothing was changed.")

    def do_edit_task(self, **changes):
        self.client.update_task(self.session, self._detail_id, **changes)
        self._reload_detail()
        self._set_status("Task updated in Odoo.")
        self.refresh()

    def checklist_items(self):
        blocks = richtext.html_to_blocks(self.detail.description) if self.detail else []
        return [(text, kind == "done") for kind, text in blocks if kind in ("check", "done")]

    def checklist_clicked(self):
        items = self.checklist_items()
        if not items:
            self._set_status("This task has no checklist in its description.")
            return
        labels = [f"{index + 1}.  {text}" for index, (text, _done) in enumerate(items)]
        ticked = [label for label, (_text, done) in zip(labels, items) if done]
        answer = self._form(
            "Checklist", [("items", "Select the items that are done", "multi", ticked, labels)], ok_text="Save in Odoo"
        )
        if answer is not None:
            states = [label in answer["items"] for label in labels]
            if states != [done for _text, done in items]:
                self._busy(lambda: self.do_set_checklist(states))

    def do_set_checklist(self, states):
        self.client.set_checklist(self.session, self._detail_id, states)
        self._reload_detail()
        self._set_status("Checklist updated in Odoo.")

    # Actions of "More from Odoo" ---------------------------------------------------------------

    def more_actions(self):
        """Buttons for the view being shown: [(label, command)]."""
        return {
            "timeoff": [
                ("Request time off", self.request_time_off_clicked),
                ("Approve", lambda: self.decide_time_off_clicked(True)),
                ("Refuse", lambda: self.decide_time_off_clicked(False)),
            ],
            "tickets": [("Move stage", self.ticket_stage_clicked), ("Plan activity", self.plan_activity_clicked)],
            "crm": [("Plan activity", self.plan_activity_clicked)],
        }.get(self.more_key, [])

    def _render_more_actions(self):
        if not hasattr(self, "more_buttons"):
            return
        for child in self.more_buttons.winfo_children():
            child.destroy()
        for label, command in reversed(self.more_actions()):
            PillButton(self.more_buttons, label, command, kind="secondary", height=32).pack(side="right", padx=(8, 0))

    def _selected_more_row(self):
        selection = self.more_tree.selection()
        row = next((r for r in self.more_rows.get(self.more_key, ()) if selection and r.key == selection[0]), None)
        if row is None:
            self.more_sub.set("Select a line first.")
        return row

    def request_time_off_clicked(self):
        ok, types = self._busy(lambda: self.client.fetch_leave_types(self.session))
        if not ok:
            return
        if not types:
            self._error("Odoo shows no time off type to this account.")
            return
        labels = unique_labels(types)
        today = self._today().isoformat()
        answer = self._form(
            "Request time off",
            [
                ("type", "Type", "combo", next(iter(labels)), list(labels)),
                ("start", "First day (YYYY-MM-DD)", "entry", today, None),
                ("end", "Last day (YYYY-MM-DD)", "entry", today, None),
                ("reason", "Reason (optional)", "entry", "", None),
            ],
            ok_text="Send request",
        )
        if answer is not None:
            self._busy(lambda: self.do_request_time_off(labels[answer["type"]], answer["start"], answer["end"], answer["reason"]))

    def do_request_time_off(self, type_id, start, end, reason=""):
        self.client.request_time_off(self.session, type_id, start, end, reason)
        self._set_status("Time off requested in Odoo.")
        self.show_more("timeoff", reload=True)

    def decide_time_off_clicked(self, approve):
        row = self._selected_more_row()
        if row is None:
            return
        if not row.key.startswith("w:"):
            self.more_sub.set("Select a line of kind “To approve”.")
            return
        verb = "Approve" if approve else "Refuse"
        if self._confirm(f"{verb} time off", f"{verb} the request of {row.cells[1]} ({row.cells[2]}, {row.cells[3]})?"):
            self._busy(lambda: self.do_decide_time_off(row.res_id, approve))

    def do_decide_time_off(self, leave_id, approve):
        self.client.decide_time_off(self.session, leave_id, approve)
        self._set_status("Request approved." if approve else "Request refused.")
        self.show_more("timeoff", reload=True)

    def ticket_stage_clicked(self):
        row = self._selected_more_row()
        if row is None:
            return
        ok, stages = self._busy(lambda: self.client.fetch_ticket_stages(self.session, row.res_id))
        if not ok or not stages:
            return
        labels = unique_labels(stages)
        current = row.cells[3] if row.cells[3] in labels else next(iter(labels))
        answer = self._form(
            "Move ticket", [("stage", f"New stage for “{row.cells[0][:40]}”", "combo", current, list(labels))], ok_text="Move"
        )
        if answer is not None and answer["stage"] != row.cells[3]:
            self._busy(lambda: self.do_ticket_stage(row.res_id, labels[answer["stage"]]))

    def do_ticket_stage(self, ticket_id, stage_id):
        self.client.set_ticket_stage(self.session, ticket_id, stage_id)
        self._set_status("Ticket moved in Odoo.")
        self.show_more("tickets", reload=True)

    def plan_activity_clicked(self):
        row = self._selected_more_row()
        if row is None or not row.model:
            return
        answer = self._form(
            "Plan activity",
            [
                ("summary", f"To do on “{row.cells[0][:40]}”", "entry", "", None),
                ("date", "Due date (YYYY-MM-DD)", "entry", self._today().isoformat(), None),
            ],
            ok_text="Plan in Odoo",
        )
        if answer is not None:
            self._busy(lambda: self.do_plan_activity(row.model, row.res_id, answer["summary"], answer["date"]))

    def do_plan_activity(self, model, res_id, summary, date):
        self.client.plan_activity(self.session, model, res_id, summary, date)
        self._set_status("Activity planned in Odoo.")
        self.load_activities(quiet=True)
        self.show_more(self.more_key, reload=True)

    # Offline mode ------------------------------------------------------------------------------

    def _show_cached(self):
        """Show the tasks saved at the last successful refresh. Returns True when there were some."""
        tasks, saved = self.store.cached_tasks()
        if not tasks:
            return False
        self.tasks = tasks
        self.open_tasks = [task for task in tasks if not task.done]
        self.health = insights.project_health(self.tasks, self._today())
        self._updated_at = saved[11:16] if len(saved) >= 16 else saved
        self._cached_at = saved.replace("T", " at ")
        self._render_all()
        return True

    def _went_offline(self, message):
        self.offline = True
        if not self._loaded and not self.tasks:
            self._show_cached()
        if self.tasks:
            when = getattr(self, "_cached_at", "") or f"today at {self._updated_at}"
            self._set_status(f"Offline: showing the tasks saved {when}. The app retries every 5 minutes.  ({message})", error=True)
        else:
            self._set_status(message, error=True)

    # One-click update ---------------------------------------------------------------------------

    def update_clicked(self):
        if not self.new_version:
            self.check_for_update(manual=True)
        elif not getattr(sys, "frozen", False) or self.on_quit is None:
            self.open_download_page()  # running from source: nothing to replace
        elif self._confirm(
            "Update Odoo Desktop",
            f"Download version {self.new_version} and restart the app now?\n\n"
            "The file comes from the app's GitHub releases page. Your settings and local data are kept.",
        ):
            version = self.new_version
            self._set_status(f"Downloading version {version}…")

            def run():
                try:
                    self.results.put(("update_ready", updates.download(version)))
                except Exception as exc:
                    self.results.put(("side_error", f"The update could not be downloaded: {exc}"))

            threading.Thread(target=run, daemon=True).start()

    def _install_update(self, path):
        try:
            updates.replace_and_restart(path, sys.executable)
        except OSError as exc:
            self._error(f"The update could not be installed: {exc}")
            return
        self.on_quit()


def _hours(hours):
    minutes = round(float(hours or 0) * 60)
    return f"{minutes // 60}:{minutes % 60:02d}" if minutes else ""


def _more_label(key):
    return {"tickets": "Ticket", "crm": "Opportunity", "milestones": "Project", "timeoff": "Time off"}.get(key, "Task")
