"""Things the dashboard can do to a task: move stage, reply, log time, plan, create, open files.

Mixed into DashboardView. Every `*_clicked` method asks the user first and then
calls the matching `do_*` method, which talks to Odoo and can be tested alone.
"""

import base64
import os
import re
import subprocess
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from . import insights, theme
from .odoo_client import OdooConnectionError
from .theme import PillButton

RISKY_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".com", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".msi", ".scr", ".lnk",
    ".hta", ".reg", ".jar", ".pif", ".cpl", ".dll",
}  # fmt: skip
INLINE_IMAGE_TYPES = ("image/png", "image/gif")
INLINE_IMAGE_MAX_BYTES = 1_500_000
INLINE_IMAGE_MAX_COUNT = 4
INLINE_IMAGE_MAX_WIDTH = 460


def human_size(size):
    if size >= 1_000_000:
        return f"{size / 1_000_000:.1f} MB"
    if size >= 1000:
        return f"{size / 1000:.0f} KB"
    return f"{size} B"


def safe_filename(name, fallback="file"):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(name)).strip(" .")
    return name[:120] or fallback


class ActionsMixin:
    # Small helpers ------------------------------------------------------

    def _confirm(self, title, message):
        return messagebox.askyesno(title, message, parent=self.root)

    def _error(self, message):
        messagebox.showerror("Odoo Desktop", message, parent=self.root)

    def _busy(self, work):
        """Run a short Odoo call with a wait cursor. Returns (ok, result)."""
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        try:
            return True, work()
        except OdooConnectionError as exc:
            self._error(str(exc))
        except Exception:
            self._error("Something went wrong while talking to Odoo.")
        finally:
            self.root.configure(cursor="")
        return False, None

    def _reload_detail(self):
        if self._detail_id is not None:
            self._render_task(self.client.fetch_task_detail(self.session, self._detail_id))

    def _form(self, title, fields, ok_text="OK"):
        """Modal form. fields: (key, label, kind, default, options); kind: entry | combo | text."""
        window = tk.Toplevel(self.root)
        window.title(title)
        window.configure(bg=theme.SURFACE)
        window.transient(self.root)
        window.resizable(False, False)
        theme.set_title_bar(window)
        body = tk.Frame(window, bg=theme.SURFACE)
        body.pack(fill="both", expand=True, padx=22, pady=18)
        readers = {}
        first = None
        for key, label, kind, default, options in fields:
            ttk.Label(body, text=label, style="Field.TLabel").pack(anchor="w", pady=(0, 4))
            if kind == "combo":
                variable = tk.StringVar(value=default)
                widget = ttk.Combobox(
                    body, textvariable=variable, values=list(options), state="readonly", width=44, font=(theme.FONT, 10)
                )
                widget.pack(fill="x", pady=(0, 12), ipady=3)
                readers[key] = variable.get
            elif kind == "multi":
                frame = tk.Frame(body, bg=theme.SURFACE)
                frame.pack(fill="x", pady=(0, 12))
                widget = tk.Listbox(
                    frame, selectmode="multiple", height=min(max(len(options), 2), 7), activestyle="none",
                    exportselection=False, relief="flat", highlightthickness=1, highlightbackground=theme.BORDER,
                    highlightcolor=theme.ACCENT, bg=theme.SURFACE_2, fg=theme.TEXT, font=(theme.FONT, 10),
                    selectbackground=theme.ACCENT, selectforeground=theme.ON_ACCENT, width=46,
                )  # fmt: skip
                for index, option in enumerate(options):
                    widget.insert("end", option)
                    if option in default:
                        widget.selection_set(index)
                widget.pack(side="left", fill="x", expand=True)
                theme.attach_scrollbar(widget, frame)
                readers[key] = lambda w=widget, o=list(options): [o[i] for i in w.curselection()]
            elif kind == "text":
                widget = tk.Text(
                    body, height=4, width=46, wrap="word", relief="flat", font=(theme.FONT, 10), fg=theme.TEXT,
                    bg=theme.SURFACE_2, insertbackground=theme.TEXT, highlightthickness=1,
                    highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT, padx=8, pady=6,
                )  # fmt: skip
                widget.insert("1.0", default)
                widget.pack(fill="x", pady=(0, 12))
                readers[key] = lambda w=widget: w.get("1.0", "end").strip()
            else:
                variable = tk.StringVar(value=default)
                widget = ttk.Entry(body, textvariable=variable, width=46, font=(theme.FONT, 10))
                widget.pack(fill="x", pady=(0, 12))
                readers[key] = lambda v=variable: v.get().strip()
            first = first or widget
        result = {}

        def accept():
            result.update({key: read() for key, read in readers.items()})
            window.destroy()

        buttons = tk.Frame(body, bg=theme.SURFACE)
        buttons.pack(fill="x", pady=(4, 0))
        PillButton(buttons, ok_text, accept, kind="primary").pack(side="right")
        PillButton(buttons, "Cancel", window.destroy, kind="secondary").pack(side="right", padx=(0, 8))
        window.bind("<Escape>", lambda _e: window.destroy())
        window.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - window.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - window.winfo_height()) // 3
        window.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        window.grab_set()
        if first is not None:
            first.focus_set()
        self.root.wait_window(window)
        return result or None

    # Task page: actions panel -------------------------------------------------

    def _build_task_actions(self, parent):
        ttk.Label(parent, text="MOVE TO STAGE", style="Caps.TLabel").pack(anchor="w")
        row = tk.Frame(parent, bg=theme.SURFACE)
        row.pack(fill="x", pady=(6, 10))
        self.stage_var = tk.StringVar()
        self.stage_box = ttk.Combobox(row, textvariable=self.stage_var, state="readonly", width=10, font=(theme.FONT, 10))
        self.stage_box.pack(side="left", fill="x", expand=True, ipady=3)
        PillButton(row, "Move", self.move_stage_clicked, kind="secondary", width=70, height=34).pack(
            side="left", padx=(8, 0)
        )
        row = tk.Frame(parent, bg=theme.SURFACE)
        row.pack(fill="x")
        row.columnconfigure(0, weight=1, uniform="half")
        row.columnconfigure(1, weight=1, uniform="half")
        self.timer_button = PillButton(
            row, "Start timer", self.toggle_timer_clicked, kind="secondary", icon=theme.ICONS["timer"], width=10, height=36
        )
        self.timer_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.task_today_button = PillButton(
            row, "Today", self.toggle_detail_today, kind="secondary", icon=theme.ICONS["today"], width=10, height=36
        )
        self.task_today_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

    def _build_reply_box(self, parent):
        tk.Frame(parent, bg=theme.BORDER, height=1).pack(fill="x", padx=12)
        row = tk.Frame(parent, bg=theme.SURFACE)
        row.pack(fill="x", padx=12, pady=10)
        self.reply_text = tk.Text(
            row, height=2, width=10, wrap="word", relief="flat", font=(theme.FONT, 10), fg=theme.TEXT,
            bg=theme.SURFACE_2, insertbackground=theme.TEXT, highlightthickness=1,
            highlightbackground=theme.BORDER, highlightcolor=theme.ACCENT, padx=10, pady=7,
        )  # fmt: skip
        self.reply_text.pack(side="left", fill="x", expand=True)
        PillButton(row, "Send", self.send_reply_clicked, kind="primary", icon=theme.ICONS["send"], height=40).pack(
            side="left", padx=(10, 0), anchor="s"
        )

    def _refresh_task_actions(self):
        detail = self.detail
        names = [name for _id, name in detail.stages] if detail else []
        self.stage_box.configure(values=names)
        self.stage_var.set(detail.stage if detail else "")
        self._refresh_timer_button()
        task_id = self._detail_id
        planned = task_id is not None and self.store.in_plan(self._today(), task_id)
        self.task_today_button.configure(text="In today" if planned else "Today")

    def _refresh_timer_button(self):
        if not hasattr(self, "timer_button") or not self.timer_button.winfo_exists():
            return
        timer = self.store.timer
        if timer and timer.get("task_id") == self._detail_id:
            self.timer_button.configure(text="Stop  " + insights.format_duration(self.store.timer_seconds()))
        elif timer:
            self.timer_button.configure(text="Other timer")
        else:
            self.timer_button.configure(text="Start timer")

    def _tick_timer(self):
        if not self._alive:
            return
        self._refresh_timer_button()
        self._refresh_timer_status()
        self.root.after(20000, self._tick_timer)

    def _refresh_timer_status(self):
        timer = self.store.timer
        if hasattr(self, "timer_var"):
            self.timer_var.set(
                f"Timer  {insights.format_duration(self.store.timer_seconds())}  ·  {timer.get('name', '')}"[:44]
                if timer
                else ""
            )

    # Move stage -----------------------------------------------------------

    def move_stage_clicked(self):
        target = self.stage_var.get()
        if not self.detail or not target or target == self.detail.stage:
            return
        if self._confirm("Move task", f"Move “{self.detail.name}” from {self.detail.stage} to {target} in Odoo?"):
            ok, _ = self._busy(lambda: self.do_set_stage(target))
            if not ok:
                self.stage_var.set(self.detail.stage)

    def do_set_stage(self, stage_name):
        stage_id = next((sid for sid, name in self.detail.stages if name == stage_name), None)
        if stage_id is None:
            raise OdooConnectionError("That stage does not exist in this project.")
        self.client.set_stage(self.session, self._detail_id, stage_id)
        self._reload_detail()
        self._set_status(f"Moved to {stage_name}.")
        self.refresh()

    # Focus: move the selected tasks ------------------------------------------

    def shared_stages(self, tasks):
        """Stage names that exist in the project of every task, and their ids per project."""
        by_project = {}
        for project_id in sorted({task.project_id for task in tasks}):
            stages = self.client.fetch_stages(self.session, project_id) if project_id else ()
            by_project[project_id] = {name: stage_id for stage_id, name in stages}
        names = None
        for stages in by_project.values():
            names = list(stages) if names is None else [name for name in names if name in stages]
        return names or [], by_project

    def move_selected_clicked(self):
        tasks = [task for task in map(self._task, self.selected_task_ids()) if task]
        if not tasks:
            return
        ok, found = self._busy(lambda: self.shared_stages(tasks))
        if not ok:
            return
        names, by_project = found
        if not names:
            self._error(
                "These tasks are in projects that have no stage in common.\n\nSelect tasks of one project."
                if len(by_project) > 1
                else "No stage was found for this project."
            )
            return
        count = len(tasks)
        label = f"{count} tasks" if count > 1 else f"“{tasks[0].name}”"
        current = tasks[0].stage if len({task.stage for task in tasks}) == 1 else ""
        answer = self._form(
            f"Move {label}",
            [("stage", f"New stage in Odoo for {label}", "combo", current if current in names else names[0], names)],
            ok_text=f"Move {count} tasks" if count > 1 else "Move",
        )
        if answer and answer.get("stage"):
            self._busy(lambda: self.do_move_tasks(tasks, answer["stage"], by_project))

    def do_move_tasks(self, tasks, stage_name, by_project):
        """One write per project. Tasks already in that stage are left alone."""
        groups = {}
        for task in tasks:
            if task.stage != stage_name:
                groups.setdefault(task.project_id, []).append(task.id)
        moved = 0
        for project_id, ids in groups.items():
            stage_id = by_project.get(project_id, {}).get(stage_name)
            if stage_id is None:
                raise OdooConnectionError("That stage does not exist in this project.")
            self.client.set_stage_many(self.session, ids, stage_id)
            moved += len(ids)
        self._set_status(f"Moved {moved} task(s) to {stage_name}." if moved else f"Already in {stage_name}.")
        self.refresh()
        return moved

    # Reply -------------------------------------------------------------------

    def send_reply_clicked(self):
        text = self.reply_text.get("1.0", "end").strip()
        if not text or not self.detail:
            return
        if self._confirm(
            "Post message", f"Post this message on “{self.detail.name}”?\n\nOdoo will notify the task's followers."
        ):
            self._busy(lambda: self.do_post_message(text))

    def do_post_message(self, text):
        self.client.post_message(self.session, self._detail_id, text)
        self.reply_text.delete("1.0", "end")
        self._reload_detail()
        self._set_status("Message posted.")

    # Timer and time logging ------------------------------------------------------

    def toggle_timer_clicked(self):
        timer = self.store.timer
        if not self.detail:
            return
        if not timer:
            self.store.start_timer(self.detail.id, self.detail.name, self.detail.project_id)
            self._refresh_timer_button()
            self._refresh_timer_status()
            self._set_status("Timer started. Stop it to log the time in Odoo.")
            return
        if timer.get("task_id") != self._detail_id:
            if self._confirm("Timer", f"A timer is running on “{timer.get('name', '')}”. Open that task?"):
                self.open_task(timer["task_id"])
            return
        hours = max(round(self.store.timer_seconds() / 3600, 2), 0.01)
        answer = self._form(
            "Log time",
            [
                ("hours", "Hours to log", "entry", f"{hours:g}", None),
                ("description", "What did you do?", "text", "", None),
            ],
            ok_text="Log in Odoo",
        )
        if answer is None:
            if self._confirm("Timer", "Discard this timer without logging any time?"):
                self.store.clear_timer()
                self._refresh_timer_button()
                self._refresh_timer_status()
            return
        try:
            value = float(answer["hours"].replace(",", "."))
        except ValueError:
            self._error("Write the hours as a number, for example 1.5")
            return
        self._busy(lambda: self.do_log_time(value, answer["description"]))

    def do_log_time(self, hours, description):
        timer = self.store.timer
        task_id = timer.get("task_id") or self._detail_id
        project_id = timer.get("project_id") or (self.detail.project_id if self.detail else 0)
        self.client.log_time(self.session, task_id, project_id, hours, description, self._today().isoformat())
        self.store.clear_timer()
        self._refresh_timer_button()
        self._refresh_timer_status()
        self._set_status(f"Logged {hours:g} h in Odoo.")
        if self._detail_id == task_id:
            self._reload_detail()

    # Today plan ------------------------------------------------------------

    def toggle_today(self, task_id):
        added = self.store.toggle_plan(self._today(), task_id)
        self._render_today()
        self._set_status("Added to today's plan." if added else "Removed from today's plan.")
        return added

    def toggle_selected_today(self):
        task_id = self.selected_task_id()
        if task_id is not None:
            added = self.toggle_today(task_id)
            self.today_button.configure(text="In today" if added else "Today")

    def toggle_detail_today(self):
        if self._detail_id is not None:
            added = self.toggle_today(self._detail_id)
            self.task_today_button.configure(text="In today" if added else "Today")

    # New task --------------------------------------------------------------------

    def new_task_clicked(self):
        ok, projects = self._busy(lambda: self._projects())
        if not ok:
            return
        if not projects:
            self._error("No project is available to this account.")
            return
        names = [name for _id, name in projects]
        default = self.project_var.get() if self.project_var.get() in names else names[0]
        answer = self._form(
            "New task",
            [
                ("project", "Project", "combo", default, names),
                ("name", "Title", "entry", "", None),
                ("deadline", "Deadline (YYYY-MM-DD, optional)", "entry", "", None),
            ],
            ok_text="Create in Odoo",
        )
        if answer is None:
            return
        project_id = next((pid for pid, name in projects if name == answer["project"]), None)
        self._busy(lambda: self.do_create_task(project_id, answer["name"], answer["deadline"]))

    def _projects(self):
        if not getattr(self, "project_list", None):
            self.project_list = self.client.fetch_projects(self.session)
        return self.project_list

    def do_create_task(self, project_id, name, deadline=""):
        task_id = self.client.create_task(self.session, project_id, name, deadline)
        self._set_status(f"Task created in Odoo (#{task_id}).")
        self.refresh()
        return task_id

    # Attachments -----------------------------------------------------------------

    def attachment_dir(self):
        folder = Path(tempfile.gettempdir()) / "OdooDesktop" / "attachments"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def save_attachment(self, attachment_id, name):
        data = self.client.download_attachment(self.session, attachment_id)
        path = self.attachment_dir() / f"{int(attachment_id)}-{safe_filename(name)}"
        path.write_bytes(data)
        return path

    def open_attachment(self, attachment_id):
        attachment = next((a for a in (self.detail.attachments if self.detail else ()) if a.id == attachment_id), None)
        if attachment is None:
            return
        ok, path = self._busy(lambda: self.save_attachment(attachment.id, attachment.name))
        if not ok:
            return
        if path.suffix.lower() in RISKY_EXTENSIONS:
            # Programs and scripts are never started from here: only shown in Explorer.
            subprocess.Popen(["explorer", "/select,", str(path)])
            self._set_status(f"Saved {path.name}. It is a program or script, so it was not opened.")
        else:
            os.startfile(str(path))  # noqa: S606 - user clicked this file
            self._set_status(f"Opened {path.name}.")

    def _load_inline_images(self, detail):
        """Fetch small PNG/GIF attachments in the background to show them in the page."""
        wanted = [
            a for a in detail.attachments if a.mimetype in INLINE_IMAGE_TYPES and 0 < a.size <= INLINE_IMAGE_MAX_BYTES
        ][:INLINE_IMAGE_MAX_COUNT]
        if not wanted:
            return

        def work():
            for attachment in wanted:
                try:
                    data = self.client.download_attachment(self.session, attachment.id)
                except Exception:
                    continue
                self.results.put(("image", (detail.id, attachment.id, data)))

        threading.Thread(target=work, daemon=True).start()

    def _show_inline_image(self, task_id, attachment_id, data):
        if task_id != self._detail_id or not self.task_text.winfo_exists():
            return
        mark = f"img:{attachment_id}"
        if mark not in self.task_text.mark_names():
            return
        try:
            image = tk.PhotoImage(data=base64.b64encode(data))
        except tk.TclError:
            return
        factor = max(1, -(-image.width() // INLINE_IMAGE_MAX_WIDTH))
        if factor > 1:
            image = image.subsample(factor)
        self._task_images.append(image)  # keep a reference or Tk drops the picture
        text = self.task_text
        text.configure(state="normal")
        text.insert(mark, "\n")
        text.image_create(mark, image=image, padx=14, pady=4)
        text.configure(state="disabled")
