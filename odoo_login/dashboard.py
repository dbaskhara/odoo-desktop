"""Dashboard shown after sign-in: sidebar + Overview, Focus, What changed, Project health."""

import csv
import datetime
import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, ttk

from . import insights, richtext, settings, theme, updates
from .actions import ActionsMixin, human_size
from .odoo_client import OdooConnectionError
from .pages import PagesMixin
from .store import LocalStore
from .theme import Card, PillButton

ALL_PROJECTS = "All projects"
AUTO_REFRESH_MS = 5 * 60 * 1000
NAV = (
    ("overview", "Overview"),
    ("today", "Today"),
    ("focus", "Focus"),
    ("calendar", "Calendar"),
    ("mentions", "Mentions"),
    ("changes", "What changed"),
    ("projects", "Project health"),
    ("team", "Team"),
    ("summary", "Weekly summary"),
    ("settings", "Settings"),
)


def bucket_colors():
    return {
        "overdue": theme.DANGER,
        "today": theme.WARN,
        "week": theme.PURPLE,
        "later": theme.ACCENT,
        "none": theme.NEUTRAL,
        "done": theme.NEUTRAL,
    }


def status_colors():
    return {
        "Late": (theme.DANGER_BG, theme.DANGER),
        "At risk": (theme.WARN_BG, theme.WARN),
        "On track": (theme.SUCCESS_BG, theme.SUCCESS),
    }


NAV_ICONS = {"projects": "health", "calendar": "calendar_page"}


class DashboardView(ActionsMixin, PagesMixin):
    def __init__(
        self, root, client, session, email, on_sign_out=None, store=None, notifier=None, on_overdue=None,
        update_checker=None,
    ):  # fmt: skip
        self.update_checker = update_checker  # callable() -> latest released version, or None: never check
        self.new_version = ""
        self._update_checked = None
        self.notifier = notifier  # callable(title, text) for Windows notifications
        self.on_overdue = on_overdue  # callable(overdue_count, open_count) for the tray tooltip
        self.root = root
        self.client = client
        self.session = session
        self.email = email
        self.on_sign_out = on_sign_out
        self.store = store or LocalStore(session)
        self.results = queue.Queue()
        self.tasks = []  # everything loaded, including tasks in a finished stage
        self.open_tasks = []
        self.visible_tasks = []
        self.changes = []
        self.health = []
        self._baseline = dict(self.store.snapshot)
        self._baseline_at = self.store.saved_at
        self._first_visit = not self.store.saved_at
        self._today = datetime.date.today
        self._alive = True
        self._loading = False
        self._loaded = False
        self._updated_at = ""
        self._note_task = None
        self.current_tab = "overview"
        self.detail = None
        self._detail_id = None
        self._task_back = "focus"
        self._init_pages_state()

        self._build_ui()
        self.root.after(100, self._poll_results)
        self.root.after(AUTO_REFRESH_MS, self._auto_refresh)
        self.root.after(20000, self._tick_timer)

    # Shell --------------------------------------------------------------

    def _build_ui(self):
        self.frame = tk.Frame(self.root, bg=theme.BG)
        self.frame.pack(fill="both", expand=True)
        self._build_sidebar()
        tk.Frame(self.frame, bg=theme.BORDER, width=1).pack(side="left", fill="y")

        main = tk.Frame(self.frame, bg=theme.BG)
        main.pack(side="left", fill="both", expand=True)
        self.status_var = tk.StringVar(value="Loading…")
        self.status_label = tk.Label(
            main, textvariable=self.status_var, bg=theme.BG, fg=theme.FAINT, font=(theme.FONT, 9), anchor="w"
        )
        self.status_label.pack(side="bottom", fill="x", padx=30, pady=(4, 12))

        holder = tk.Frame(main, bg=theme.BG)
        holder.pack(fill="both", expand=True, padx=30, pady=(24, 0))
        self.pages = {}
        for key in [key for key, _label in NAV] + ["task"]:
            page = tk.Frame(holder, bg=theme.BG)
            page.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.pages[key] = page
        self._build_task_page(self.pages["task"])
        self._build_overview_page(self.pages["overview"])
        self._build_focus_page(self.pages["focus"])
        self._build_changes_page(self.pages["changes"])
        self._build_projects_page(self.pages["projects"])
        for key in ("today", "calendar", "mentions", "team", "summary", "settings"):
            getattr(self, f"_build_{key}_page")(self.pages[key])
        self.show_tab(self.current_tab)

    def _build_sidebar(self):
        side = tk.Frame(self.frame, bg=theme.SIDEBAR, width=232)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        brand = tk.Frame(side, bg=theme.SIDEBAR)
        brand.pack(fill="x", padx=18, pady=(22, 24))
        theme.logo(brand, theme.SIDEBAR, 38).pack(side="left")
        names = tk.Frame(brand, bg=theme.SIDEBAR)
        names.pack(side="left", padx=(12, 0))
        tk.Label(names, text="Odoo Desktop", bg=theme.SIDEBAR, fg=theme.TEXT, font=(theme.FONT_BOLD, 12)).pack(anchor="w")
        host = self.session.base_url.split("://", 1)[-1]
        tk.Label(names, text=host, bg=theme.SIDEBAR, fg=theme.FAINT, font=(theme.FONT, 8)).pack(anchor="w")

        tk.Label(side, text="MENU", bg=theme.SIDEBAR, fg=theme.FAINT, font=(theme.FONT_BOLD, 8)).pack(
            anchor="w", padx=22, pady=(0, 6)
        )
        self.nav_rows = {}
        self.tab_text = {}
        for key, label in NAV:
            var = tk.StringVar(value=label)
            row = tk.Frame(side, bg=theme.SIDEBAR, cursor="hand2")
            row.pack(fill="x", padx=(0, 12), pady=1)
            bar = tk.Frame(row, bg=theme.SIDEBAR, width=3)
            bar.pack(side="left", fill="y", pady=6)
            pill = tk.Frame(row, bg=theme.SIDEBAR)
            pill.pack(side="left", fill="x", expand=True, padx=(9, 0))
            icon = theme.icon_label(pill, NAV_ICONS.get(key, key), theme.SIDEBAR)
            icon.pack(side="left", padx=(10, 10), pady=7)
            text = tk.Label(pill, textvariable=var, bg=theme.SIDEBAR, fg=theme.MUTED, font=(theme.FONT, 10))
            text.pack(side="left")
            for widget in (row, pill, icon, text):
                widget.bind("<Button-1>", lambda _e, k=key: self.nav_clicked(k))
            self.nav_rows[key] = (bar, pill, icon, text)
            self.tab_text[key] = var

        foot = tk.Frame(side, bg=theme.SIDEBAR)
        foot.pack(side="bottom", fill="x", padx=18, pady=(0, 16))
        self.update_label = tk.Label(
            foot, bg=theme.SIDEBAR, fg=theme.WARN, font=(theme.FONT_BOLD, 9), anchor="w", cursor="hand2"
        )
        self.update_label.bind("<Button-1>", lambda _e: self.open_download_page())
        self.timer_var = tk.StringVar()
        self.timer_label = tk.Label(
            foot, textvariable=self.timer_var, bg=theme.SIDEBAR, fg=theme.ACCENT, font=(theme.FONT, 8), anchor="w"
        )
        self.timer_label.pack(fill="x", pady=(0, 4))
        self._show_update_notice()
        self._refresh_timer_status()
        tk.Label(
            foot, text=self.email, bg=theme.SIDEBAR, fg=theme.TEXT, font=(theme.FONT_BOLD, 9), anchor="w"
        ).pack(fill="x")
        self.updated_var = tk.StringVar(value=self.session.database)
        if self._updated_at:
            self.updated_var.set(f"{self.session.database} · updated {self._updated_at}")
        tk.Label(
            foot, textvariable=self.updated_var, bg=theme.SIDEBAR, fg=theme.FAINT, font=(theme.FONT, 8), anchor="w"
        ).pack(fill="x", pady=(1, 12))
        tools = tk.Frame(foot, bg=theme.SIDEBAR)
        tools.pack(fill="x")
        PillButton(
            tools, "", self.toggle_theme, kind="ghost", outer=theme.SIDEBAR,
            icon=theme.ICONS["sun" if theme.MODE == "dark" else "moon"], width=36, height=34,
        ).pack(side="left")  # fmt: skip
        PillButton(
            tools, "", self.refresh, kind="ghost", outer=theme.SIDEBAR, icon=theme.ICONS["refresh"], width=36, height=34
        ).pack(side="left", padx=(4, 0))
        if self.on_sign_out:
            PillButton(
                tools, "Sign out", self._sign_out, kind="ghost", outer=theme.SIDEBAR, height=34
            ).pack(side="right")

    def show_tab(self, key):
        self.current_tab = key
        highlighted = self._task_back if key == "task" else key
        for name, (bar, pill, icon, text) in self.nav_rows.items():
            active = name == highlighted
            bg = theme.ACCENT_SOFT if active else theme.SIDEBAR
            bar.configure(bg=theme.ACCENT if active else theme.SIDEBAR)
            pill.configure(bg=bg)
            icon.configure(bg=bg, fg=theme.ACCENT if active else theme.MUTED)
            text.configure(bg=bg, fg=theme.TEXT if active else theme.MUTED, font=(theme.FONT_BOLD if active else theme.FONT, 10))
        self.pages[key].lift()
        self._on_show(key)

    def _page_header(self, page, title, subtitle, action=None):
        head = tk.Frame(page, bg=theme.BG)
        head.pack(fill="x", pady=(0, 18))
        left = tk.Frame(head, bg=theme.BG)
        left.pack(side="left")
        ttk.Label(left, text=title, style="PageTitle.TLabel").pack(anchor="w")
        if isinstance(subtitle, tk.Variable):
            ttk.Label(left, textvariable=subtitle, style="PageSub.TLabel").pack(anchor="w", pady=(2, 0))
        else:
            ttk.Label(left, text=subtitle, style="PageSub.TLabel").pack(anchor="w", pady=(2, 0))
        if action:
            text, command, kind, icon = action
            PillButton(head, text, command, kind=kind, outer=theme.BG, icon=theme.ICONS[icon]).pack(side="right")
        return head

    def toggle_theme(self):
        mode = theme.set_mode("light" if theme.MODE == "dark" else "dark")
        settings.save({"theme": mode})
        theme.apply_styles(self.root)
        theme.set_title_bar(self.root)
        self.rebuild()

    def rebuild(self):
        self._save_note()
        self._save_task_note()
        self._note_task = None
        self.health_canvas.unbind_all("<MouseWheel>")
        self.frame.destroy()
        self._build_ui()
        if self._loaded:
            self._render_all()
        if self.current_tab == "task" and self.detail:
            self._load_task_note()
            self._render_task(self.detail)

    # Overview page ------------------------------------------------------

    def _build_overview_page(self, page):
        head = self._page_header(
            page,
            "Overview",
            "A calm view of your Odoo work — at a glance.",
            action=("Refresh", self.refresh, "secondary", "refresh"),
        )
        PillButton(
            head, "New task", self.new_task_clicked, kind="primary", outer=theme.BG, icon=theme.ICONS["add"]
        ).pack(side="right", padx=(0, 8))
        row = tk.Frame(page, bg=theme.BG)
        row.pack(fill="x")
        self.stat_vars = {}
        tiles = (
            ("open", "Open tasks", "tasks", theme.TEXT, None),
            ("overdue", "Overdue", "warning", theme.DANGER, None),
            ("week", "Due in 7 days", "calendar", theme.WARN, None),
            ("stale", f"Untouched {insights.STALE_DAYS}+ days", "idle", theme.TEXT, self._show_untouched),
            ("changes", "Changes", "changes", theme.ACCENT, lambda: self.show_tab("changes")),
        )
        for index, (key, label, icon, color, command) in enumerate(tiles):
            row.columnconfigure(index, weight=1, uniform="stats")
            card = Card(row, padding=(16, 14))
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 12, 0))
            top = tk.Frame(card.body, bg=theme.SURFACE)
            top.pack(fill="x")
            theme.icon_label(top, icon, theme.SURFACE, theme.FAINT, 9).pack(side="left")
            tk.Label(top, text=label.upper(), bg=theme.SURFACE, fg=theme.FAINT, font=(theme.FONT_BOLD, 8)).pack(
                side="left", padx=(7, 0)
            )
            var = tk.StringVar(value="—")
            tk.Label(card.body, textvariable=var, bg=theme.SURFACE, fg=color, font=(theme.FONT_BOLD, 22)).pack(
                anchor="w", pady=(8, 0)
            )
            self.stat_vars[key] = var
            card.make_clickable(command or (lambda: self.show_tab("focus")))

        ttk.Label(page, text="QUICK ACTIONS", style="Section.TLabel").pack(anchor="w", pady=(22, 8))
        actions = tk.Frame(page, bg=theme.BG)
        actions.pack(fill="x")
        self.changes_hint = tk.StringVar(value="Since your last visit")
        quick = (
            ("focus", "Open Focus", "All your tasks, by urgency", lambda: self.show_tab("focus")),
            ("changes", "See what changed", self.changes_hint, lambda: self.show_tab("changes")),
            ("export", "Export to CSV", "Save the task list for Excel", self.export_csv),
        )
        for index, (icon, title, sub, command) in enumerate(quick):
            actions.columnconfigure(index, weight=1, uniform="quick")
            card = Card(actions, padding=(14, 12))
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 12, 0))
            tile = tk.Canvas(card.body, width=38, height=38, bg=theme.SURFACE, highlightthickness=0)
            theme.round_rect(tile, 0, 0, 38, 38, 10, fill=theme.ACCENT_SOFT, outline=theme.ACCENT_SOFT)
            tile.create_text(19, 20, text=theme.ICONS[icon], fill=theme.ACCENT, font=(theme.ICON_FONT, 12))
            tile.pack(side="left")
            texts = tk.Frame(card.body, bg=theme.SURFACE)
            texts.pack(side="left", padx=(12, 0))
            tk.Label(texts, text=title, bg=theme.SURFACE, fg=theme.TEXT, font=(theme.FONT_BOLD, 10)).pack(anchor="w")
            options = {"textvariable": sub} if isinstance(sub, tk.Variable) else {"text": sub}
            tk.Label(texts, bg=theme.SURFACE, fg=theme.MUTED, font=(theme.FONT, 9), **options).pack(anchor="w")
            card.make_clickable(command)

        ttk.Label(page, text="NEEDS ATTENTION NOW", style="Section.TLabel").pack(anchor="w", pady=(22, 8))
        card = Card(page, padding=(8, 8))
        card.pack(fill="both", expand=True)
        self.attention_box = card.body

    def _render_attention(self):
        for child in self.attention_box.winfo_children():
            child.destroy()
        today = self._today()
        order = [key for key, _label in insights.BUCKETS]
        urgent = [t for t in self.tasks if insights.bucket_of(t, today) in ("overdue", "today", "week")]
        urgent.sort(key=lambda t: (order.index(insights.bucket_of(t, today)), t.deadline, t.name.lower()))
        if not urgent:
            tk.Label(
                self.attention_box,
                text="Nothing urgent. No task is overdue or due in the next 7 days.",
                bg=theme.SURFACE,
                fg=theme.MUTED,
                font=(theme.FONT, 10),
            ).pack(anchor="w", padx=12, pady=14)
            return
        colors = bucket_colors()
        shown = urgent[:6]
        for task in shown:
            bucket = insights.bucket_of(task, today)
            row = tk.Frame(self.attention_box, bg=theme.SURFACE, cursor="hand2")
            row.pack(fill="x")
            dot = tk.Canvas(row, width=12, height=12, bg=theme.SURFACE, highlightthickness=0)
            dot.create_oval(1, 1, 11, 11, fill=colors[bucket], outline="")
            dot.pack(side="left", padx=(12, 12), pady=10)
            texts = tk.Frame(row, bg=theme.SURFACE)
            texts.pack(side="left", fill="x", expand=True)
            name = tk.Label(texts, text=task.name, bg=theme.SURFACE, fg=theme.TEXT, font=(theme.FONT_BOLD, 10), anchor="w")
            name.pack(fill="x")
            sub = tk.Label(
                texts, text=f"{task.project}  ·  {task.stage}", bg=theme.SURFACE, fg=theme.MUTED, font=(theme.FONT, 9), anchor="w"
            )
            sub.pack(fill="x")
            when = tk.Label(
                row,
                text=insights.deadline_text(task, today),
                bg=theme.SURFACE,
                fg=colors[bucket] if bucket != "week" else theme.MUTED,
                font=(theme.FONT_BOLD, 9),
            )
            when.pack(side="right", padx=14)
            widgets = (row, dot, texts, name, sub, when)
            for widget in widgets:
                widget.bind("<Button-1>", lambda _e, tid=task.id: self.show_task(tid))
                widget.bind("<Enter>", lambda _e, ws=widgets: [w.configure(bg=theme.SURFACE_2) for w in ws])
                widget.bind("<Leave>", lambda _e, ws=widgets: [w.configure(bg=theme.SURFACE) for w in ws])
        if len(urgent) > len(shown):
            more = tk.Label(
                self.attention_box,
                text=f"+ {len(urgent) - len(shown)} more in Focus",
                bg=theme.SURFACE,
                fg=theme.ACCENT,
                font=(theme.FONT, 9),
                cursor="hand2",
            )
            more.pack(anchor="w", padx=36, pady=(4, 6))
            more.bind("<Button-1>", lambda _e: self.show_tab("focus"))

    def _show_untouched(self):
        self._reset_filters()
        self.stale_only.set(True)
        self.render_focus()
        self.show_tab("focus")

    # Focus page ---------------------------------------------------------

    def _build_focus_page(self, page):
        self.focus_sub = tk.StringVar(value="Every open task assigned to you, grouped by urgency.")
        self._page_header(
            page,
            "Focus",
            self.focus_sub,
            action=("Export CSV", self.export_csv, "secondary", "export"),
        )
        body = tk.Frame(page, bg=theme.BG)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        card = Card(body, padding=(16, 14))
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 14))

        tools = ttk.Frame(card.body)
        tools.pack(fill="x", pady=(0, 10))
        self.search_var = tk.StringVar()
        search = ttk.Entry(tools, textvariable=self.search_var, width=18, font=(theme.FONT, 10))
        search.pack(side="left")
        hint = tk.Label(tools, text="Search tasks…", bg=theme.SURFACE_2, fg=theme.FAINT, font=(theme.FONT, 10))
        hint.bind("<Button-1>", lambda _e: search.focus_set())

        def refresh_hint(*_):
            if self.search_var.get():
                hint.place_forget()
            else:
                hint.place(in_=search, x=11, rely=0.5, anchor="w")

        search.bind("<Configure>", refresh_hint, add="+")
        self.project_var = tk.StringVar(value=ALL_PROJECTS)
        self.project_box = ttk.Combobox(
            tools, textvariable=self.project_var, values=[ALL_PROJECTS], state="readonly", width=17,
            font=(theme.FONT, 10),
        )
        self.project_box.pack(side="left", padx=(10, 0), ipady=3)
        self.pinned_only = tk.BooleanVar(value=False)
        self.stale_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(tools, text="Pinned", variable=self.pinned_only, command=self.render_focus).pack(
            side="left", padx=(14, 0)
        )
        ttk.Checkbutton(tools, text="Untouched", variable=self.stale_only, command=self.render_focus).pack(
            side="left", padx=(10, 0)
        )
        self.show_done = tk.BooleanVar(value=False)
        ttk.Checkbutton(tools, text="Show done", variable=self.show_done, command=self.render_focus).pack(
            side="left", padx=(10, 0)
        )
        self.search_var.trace_add("write", lambda *_: (refresh_hint(), self.render_focus()))
        self.project_box.bind("<<ComboboxSelected>>", lambda _e: self.render_focus())

        tk.Frame(card.body, bg=theme.BORDER, height=1).pack(fill="x")
        box = ttk.Frame(card.body)
        box.pack(fill="both", expand=True)
        self.focus_tree = ttk.Treeview(
            box, columns=("project", "stage", "deadline", "idle"), show="tree headings", selectmode="extended"
        )
        self.focus_tree.heading("#0", text="TASK", anchor="w")
        self.focus_tree.heading("project", text="PROJECT", anchor="w")
        self.focus_tree.heading("stage", text="STAGE", anchor="w")
        self.focus_tree.heading("deadline", text="DEADLINE", anchor="w")
        self.focus_tree.heading("idle", text="UNTOUCHED", anchor="w")
        self.focus_tree.column("#0", width=190, minwidth=160, anchor="w", stretch=True)
        self.focus_tree.column("project", width=120, minwidth=90, anchor="w", stretch=True)
        self.focus_tree.column("stage", width=100, minwidth=80, anchor="w", stretch=True)
        self.focus_tree.column("deadline", width=92, anchor="w", stretch=False)
        self.focus_tree.column("idle", width=100, anchor="w", stretch=False)
        self.focus_tree.tag_configure(
            "group", font=(theme.FONT_BOLD, 8), foreground=theme.MUTED, background=theme.SURFACE_2
        )
        self.focus_tree.tag_configure("overdue", foreground=theme.DANGER)
        self.focus_tree.tag_configure("today", foreground=theme.WARN)
        self.focus_tree.tag_configure("done", foreground=theme.FAINT)
        self.focus_tree.pack(side="left", fill="both", expand=True)
        theme.attach_scrollbar(self.focus_tree, box)
        self.focus_tree.bind("<<TreeviewSelect>>", lambda _e: self._show_detail())
        self.focus_tree.bind("<Double-1>", lambda _e: self.view_selected_task())

        self._build_detail_panel(body)

    def _build_detail_panel(self, parent):
        card = Card(parent, padding=(18, 16), fixed_width=296)
        card.grid(row=0, column=1, sticky="nsew")
        body = card.body

        self.detail_title = tk.StringVar(value="Select a task")
        ttk.Label(body, textvariable=self.detail_title, style="Heading.TLabel", wraplength=252, justify="left").pack(
            anchor="w"
        )
        self.detail_meta = tk.StringVar()
        ttk.Label(body, textvariable=self.detail_meta, style="Sub.TLabel", wraplength=252, justify="left").pack(
            anchor="w", pady=(6, 16)
        )

        ttk.Label(body, text="MY PRIVATE NOTE", style="Caps.TLabel").pack(anchor="w")
        self.note_text = tk.Text(
            body,
            height=6,
            width=10,
            wrap="word",
            relief="flat",
            font=(theme.FONT, 10),
            fg=theme.TEXT,
            bg=theme.SURFACE_2,
            insertbackground=theme.TEXT,
            selectbackground=theme.ACCENT,
            selectforeground=theme.ON_ACCENT,
            highlightthickness=1,
            highlightbackground=theme.BORDER,
            highlightcolor=theme.ACCENT,
            padx=10,
            pady=8,
            state="disabled",
        )
        self.note_text.pack(fill="both", expand=True, pady=(6, 6))
        self.note_text.bind("<FocusOut>", lambda _e: self._save_note())
        ttk.Label(body, text="Saved on this computer only, never sent to Odoo.", style="Hint.TLabel", wraplength=252).pack(
            anchor="w", pady=(0, 14)
        )

        self.view_button = PillButton(
            body, "View full task", self.view_selected_task, kind="primary", icon=theme.ICONS["view"]
        )
        self.view_button.pack(fill="x", pady=(0, 8))
        row = tk.Frame(body, bg=theme.SURFACE)
        row.pack(fill="x")
        row.columnconfigure(0, weight=1, uniform="half")
        row.columnconfigure(1, weight=1, uniform="half")
        row.columnconfigure(2, weight=1, uniform="half")
        self.pin_button = PillButton(row, "Pin", self.toggle_pin, kind="secondary", width=10)
        self.pin_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.today_button = PillButton(row, "Today", self.toggle_selected_today, kind="secondary", width=10)
        self.today_button.grid(row=0, column=1, sticky="ew", padx=(2, 2))
        self.open_button = PillButton(row, "Odoo", self.open_selected_task, kind="secondary", width=10)
        self.open_button.grid(row=0, column=2, sticky="ew", padx=(4, 0))
        self.move_button = PillButton(body, "Move to stage", self.move_selected_clicked, kind="secondary")
        self.move_button.pack(fill="x", pady=(8, 0))
        self._show_detail()

    # Changes page -------------------------------------------------------

    def _build_changes_page(self, page):
        self.changes_sub = tk.StringVar(value="What happened to your tasks since your last visit.")
        self._page_header(
            page, "What changed", self.changes_sub, action=("Mark as seen", self.mark_changes_seen, "secondary", "today")
        )
        card = Card(page, padding=(16, 10))
        card.pack(fill="both", expand=True)
        box = ttk.Frame(card.body)
        box.pack(fill="both", expand=True)
        self.changes_tree = ttk.Treeview(box, columns=("change", "task", "project", "detail"), show="headings")
        for name, text, width in (
            ("change", "CHANGE", 150),
            ("task", "TASK", 260),
            ("project", "PROJECT", 160),
            ("detail", "DETAIL", 220),
        ):
            self.changes_tree.heading(name, text=text, anchor="w")
            self.changes_tree.column(name, width=width, anchor="w")
        self.changes_tree.tag_configure("new", foreground=theme.ACCENT)
        self.changes_tree.tag_configure("gone", foreground=theme.FAINT)
        self.changes_tree.tag_configure("deadline", foreground=theme.WARN)
        self.changes_tree.pack(side="left", fill="both", expand=True)
        theme.attach_scrollbar(self.changes_tree, box)
        self.changes_tree.bind("<Double-1>", lambda _e: self._view_change())

    def mark_changes_seen(self):
        """Empty the list: from now on, changes are counted from this moment."""
        if not self._loaded:
            return
        self._baseline = dict(self.store.snapshot)
        self._baseline_at = self.store.saved_at
        self._first_visit = False
        self.changes = []
        self._render_all()

    # Projects page ------------------------------------------------------

    def _build_projects_page(self, page):
        head = self._page_header(
            page, "Project health", "Based on your own open tasks. Click a project to see its tasks in Focus."
        )
        legend = tk.Frame(head, bg=theme.BG)
        legend.pack(side="right", anchor="s")
        colors = bucket_colors()
        for key, label in insights.OPEN_BUCKETS:
            dot = tk.Canvas(legend, width=10, height=10, bg=theme.BG, highlightthickness=0)
            dot.create_oval(1, 1, 9, 9, fill=colors[key], outline="")
            dot.pack(side="left", padx=(14, 5))
            tk.Label(legend, text=label, bg=theme.BG, fg=theme.MUTED, font=(theme.FONT, 9)).pack(side="left")

        card = Card(page, padding=(16, 8))
        card.pack(fill="both", expand=True)
        box = ttk.Frame(card.body)
        box.pack(fill="both", expand=True)
        self.health_canvas = tk.Canvas(box, bg=theme.SURFACE, highlightthickness=0)
        self.health_canvas.pack(side="left", fill="both", expand=True)
        theme.attach_scrollbar(self.health_canvas, box)
        self.health_rows = tk.Frame(self.health_canvas, bg=theme.SURFACE)
        window = self.health_canvas.create_window((0, 0), window=self.health_rows, anchor="nw")
        self.health_rows.bind(
            "<Configure>", lambda _e: self.health_canvas.configure(scrollregion=self.health_canvas.bbox("all"))
        )
        self.health_canvas.bind("<Configure>", lambda e: self.health_canvas.itemconfigure(window, width=e.width))
        self.health_canvas.bind(
            "<Enter>", lambda _e: self.health_canvas.bind_all("<MouseWheel>", self._scroll_health)
        )
        self.health_canvas.bind("<Leave>", lambda _e: self.health_canvas.unbind_all("<MouseWheel>"))

    def _scroll_health(self, event):
        if self.health_rows.winfo_height() > self.health_canvas.winfo_height():
            self.health_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _render_health(self):
        for child in self.health_rows.winfo_children():
            child.destroy()
        if not self.health:
            tk.Label(
                self.health_rows, text="No open tasks assigned to you.", bg=theme.SURFACE, fg=theme.MUTED
            ).pack(anchor="w", pady=20)
            return
        chips = status_colors()
        for index, project in enumerate(self.health):
            if index:
                tk.Frame(self.health_rows, bg=theme.BORDER, height=1).pack(fill="x")
            row = tk.Frame(self.health_rows, bg=theme.SURFACE, cursor="hand2")
            row.pack(fill="x", pady=12, padx=4)
            top = tk.Frame(row, bg=theme.SURFACE)
            top.pack(fill="x")
            name = tk.Label(top, text=project.name, bg=theme.SURFACE, fg=theme.TEXT, font=(theme.FONT_BOLD, 10))
            name.pack(side="left")
            bg, fg = chips[project.status]
            chip = tk.Label(top, text=project.status, bg=bg, fg=fg, font=(theme.FONT_BOLD, 8), padx=9, pady=3)
            chip.pack(side="right")
            bar = tk.Canvas(row, height=8, bg=theme.SURFACE, highlightthickness=0)
            bar.pack(fill="x", pady=(8, 7))
            bar.bind("<Configure>", lambda event, p=project, c=bar: self._draw_bar(c, p, event.width))
            parts = [f"{project.open} open"]
            if project.overdue:
                parts.append(f"{project.overdue} overdue")
            if project.today + project.week:
                parts.append(f"{project.today + project.week} due in 7 days")
            if project.stale:
                parts.append(f"{project.stale} untouched {insights.STALE_DAYS}+ days")
            info = tk.Label(row, text="   ·   ".join(parts), bg=theme.SURFACE, fg=theme.MUTED, font=(theme.FONT, 9))
            info.pack(anchor="w")
            for widget in (row, top, name, chip, bar, info):
                widget.bind("<Button-1>", lambda _e, n=project.name: self.focus_on_project(n))

    @staticmethod
    def _draw_bar(canvas, project, width):
        canvas.delete("all")
        theme.round_rect(canvas, 0, 0, width, 8, 4, fill=theme.SURFACE_2, outline=theme.SURFACE_2)
        colors = bucket_colors()
        x = 0
        for key, _label in insights.OPEN_BUCKETS:
            count = getattr(project, key)
            if not count:
                continue
            w = width * count / project.open
            canvas.create_rectangle(x, 0, x + w - 2, 8, fill=colors[key], outline="")
            x += w

    def _reset_filters(self):
        self.search_var.set("")
        self.pinned_only.set(False)
        self.stale_only.set(False)
        self.show_done.set(False)
        self.day_filter = None
        self.project_var.set(ALL_PROJECTS)

    def focus_on_project(self, name):
        self._reset_filters()
        self.project_var.set(name)
        self.render_focus()
        self.show_tab("focus")

    def show_task(self, task_id):
        self._reset_filters()
        task = self._task(task_id)
        self.show_done.set(bool(task and task.done))
        self.render_focus()
        iid = str(task_id)
        if self.focus_tree.exists(iid):
            self.focus_tree.selection_set(iid)
            self.focus_tree.see(iid)
        self.show_tab("focus")

    # Data loading -------------------------------------------------------

    def refresh(self):
        if self._loading:
            return
        self._loading = True
        self._set_status("Loading your tasks…")
        threading.Thread(target=self._worker, daemon=True).start()

    def _auto_refresh(self):
        if not self._alive:
            return
        self.refresh()
        self.root.after(AUTO_REFRESH_MS, self._auto_refresh)

    def _worker(self):
        try:
            tasks = self.client.fetch_my_tasks(self.session)
            self.results.put(("tasks", tasks))
        except OdooConnectionError as exc:
            self.results.put(("error", str(exc)))
            return
        except Exception:
            self.results.put(("error", "Could not load tasks from Odoo."))
            return
        try:
            open_ids = [task.id for task in tasks if not task.done]
            self.results.put(("replies", self.client.fetch_new_replies(self.session, open_ids, self._replies_since)))
        except Exception:
            pass  # notifications are a bonus: never fail a refresh because of them

    def load_sync(self):
        self.apply_tasks(self.client.fetch_my_tasks(self.session))

    def _poll_results(self):
        if not self._alive:
            return
        try:
            kind, payload = self.results.get_nowait()
        except queue.Empty:
            self.root.after(150, self._poll_results)
            return
        was_loading = self._loading
        self._loading = False
        if kind == "detail":
            self._loading = was_loading
            if payload.id == self._detail_id:
                self._render_task(payload)
        elif kind == "detail_error":
            self._loading = was_loading
            if payload[0] == self._detail_id:
                self.task_sub.set("Could not load this task.")
                self._fill_task_text([("error", payload[1]), ("muted", "You can still try Open in Odoo.")])
        elif kind == "tasks":
            self.apply_tasks(payload)
        elif kind == "error":
            self._set_status(payload, error=True)
        else:
            self._loading = was_loading
            self._handle_side_result(kind, payload)
        self.root.after(150, self._poll_results)

    def apply_tasks(self, tasks):
        self._save_note()
        previous_ids = {task.id for task in self.open_tasks} if self._loaded else None
        self.tasks = list(tasks)
        self.open_tasks = [task for task in self.tasks if not task.done]
        current = insights.snapshot(self.tasks)
        self.changes = [] if self._first_visit else insights.diff(self._baseline, current)
        if self.store.saved_at:
            self.store.add_history(insights.diff(self.store.snapshot, current), self._today())
        self.store.remember_snapshot(current)
        self.health = insights.project_health(self.tasks, self._today())
        self._loaded = True
        self._updated_at = datetime.datetime.now().strftime("%H:%M")
        self._render_all()
        self._after_load(previous_ids)
        self.check_for_update()

    def _render_all(self):
        today = self._today()
        buckets = [insights.bucket_of(task, today) for task in self.open_tasks]
        self.stat_vars["open"].set(str(len(self.open_tasks)))
        self.stat_vars["overdue"].set(str(buckets.count("overdue")))
        self.stat_vars["week"].set(str(buckets.count("today") + buckets.count("week")))
        self.stat_vars["stale"].set(str(sum(1 for task in self.open_tasks if insights.is_stale(task, today))))
        self.stat_vars["changes"].set("—" if self._first_visit else str(len(self.changes)))

        names = sorted({task.project for task in self.tasks}, key=str.lower)
        self.project_box.configure(values=[ALL_PROJECTS] + names)
        if self.project_var.get() not in names:
            self.project_var.set(ALL_PROJECTS)

        self.render_focus()
        self._render_changes()
        self._render_health()
        self._render_attention()
        self._render_extra()
        self.updated_var.set(f"{self.session.database} · updated {self._updated_at}")
        hidden = len(self.tasks) - len(self.open_tasks)
        self._set_status(
            f"{len(self.open_tasks)} open task(s) in {len(self.health)} project(s)."
            + (f" {hidden} in a done stage are hidden." if hidden else "")
            + " Refreshes every 5 minutes."
        )

    # Rendering ----------------------------------------------------------

    def _filtered_tasks(self):
        today = self._today()
        needle = self.search_var.get().strip().lower()
        project = self.project_var.get()
        result = []
        for task in self.tasks:
            if self.day_filter and task.deadline != self.day_filter:
                continue
            if task.done and not self.show_done.get():
                continue
            if project != ALL_PROJECTS and task.project != project:
                continue
            if self.pinned_only.get() and task.id not in self.store.pins:
                continue
            if self.stale_only.get() and not insights.is_stale(task, today):
                continue
            if needle and needle not in f"{task.name} {task.project} {task.stage}".lower():
                continue
            result.append(task)
        return result

    def _marks(self, task, has_note=None):
        has_note = bool(self.store.note(task.id)) if has_note is None else has_note
        return ("★ " if task.id in self.store.pins else "") + ("✎ " if has_note else "")

    def render_focus(self):
        today = self._today()
        selected = [str(task_id) for task_id in self.selected_task_ids()]
        tree = self.focus_tree
        tree.delete(*tree.get_children())
        groups = {key: [] for key, _label in insights.BUCKETS}
        self.visible_tasks = self._filtered_tasks()
        self.focus_sub.set(
            f"Only tasks due on {self.day_filter}. Click Focus in the menu to show all tasks again."
            if self.day_filter
            else "Every open task assigned to you, grouped by urgency."
        )
        for task in self.visible_tasks:
            groups[insights.bucket_of(task, today)].append(task)
        for key, label in insights.BUCKETS:
            items = groups[key]
            if not items:
                continue
            items.sort(
                key=lambda t: (t.id not in self.store.pins, not t.starred, t.deadline or "9999", t.name.lower())
            )
            parent = tree.insert(
                "", "end", iid=f"g:{key}", text=f"{label.upper()}   {len(items)}", open=True, tags=("group",)
            )
            for task in items:
                tree.insert(
                    parent,
                    "end",
                    iid=str(task.id),
                    text=self._marks(task) + task.name,
                    values=(
                        task.project,
                        task.stage,
                        insights.deadline_text(task, today),
                        insights.idle_text(task, today),
                    ),
                    tags=(key,) if key in ("overdue", "today", "done") else (),
                )
        selected = [iid for iid in selected if tree.exists(iid)]
        if selected:
            tree.selection_set(selected)
        else:
            self._show_detail()

    def _render_changes(self):
        tree = self.changes_tree
        tree.delete(*tree.get_children())
        count = len(self.changes)
        self.tab_text["changes"].set(f"What changed ({count})" if count else "What changed")
        if self._first_visit:
            self.changes_sub.set("This is your first visit on this computer. Changes will show up here next time.")
            self.changes_hint.set("Available from your next visit")
            return
        since = self._baseline_at.replace("T", " at ") if self._baseline_at else "your last visit"
        if count:
            self.changes_sub.set(f"{count} change(s) on your tasks since {since}. Double-click to read the task.")
            self.changes_hint.set(f"{count} change(s) since your last visit")
        else:
            self.changes_sub.set(f"Nothing changed on your tasks since {since}.")
            self.changes_hint.set("Nothing new since your last visit")
        for index, change in enumerate(self.changes):
            tree.insert(
                "",
                "end",
                iid=f"{index}:{change.task_id}",
                values=(insights.CHANGE_LABELS[change.kind], change.name, change.project, change.detail),
                tags=(change.kind,),
            )

    # Task detail, pins, notes -------------------------------------------

    def selected_task_ids(self):
        """Every task selected in Focus (group headers are ignored)."""
        return [int(iid) for iid in self.focus_tree.selection() if not iid.startswith("g:")]

    def selected_task_id(self):
        """The selected task when exactly one is selected."""
        ids = self.selected_task_ids()
        return ids[0] if len(ids) == 1 else None

    def _task(self, task_id):
        return next((task for task in self.tasks if task.id == task_id), None)

    def _show_detail(self):
        self._save_note()
        several = [task for task in map(self._task, self.selected_task_ids()) if task]
        task = several[0] if len(several) == 1 else None
        self.note_text.configure(state="normal")
        self.note_text.delete("1.0", "end")
        self.move_button.configure(
            state="normal" if several else "disabled",
            text=f"Move {len(several)} tasks to stage" if len(several) > 1 else "Move to stage",
        )
        if len(several) > 1:
            self._note_task = None
            self.detail_title.set(f"{len(several)} tasks selected")
            projects = sorted({t.project for t in several})
            stages = sorted({t.stage for t in several})
            self.detail_meta.set(
                f"Project:  {', '.join(projects)}\nStage:  {', '.join(stages)}\n\n"
                "Move them all to one stage with the button below."
            )
            self.note_text.configure(state="disabled")
            self.pin_button.configure(state="disabled", text="Pin")
            self.open_button.configure(state="disabled")
            self.view_button.configure(state="disabled")
            self.today_button.configure(state="disabled", text="Today")
            return
        if task is None:
            self._note_task = None
            self.detail_title.set("Select a task")
            self.detail_meta.set("Pick a task in the list to read it in full, pin it or add a private note.")
            self.note_text.configure(state="disabled")
            self.pin_button.configure(state="disabled", text="Pin")
            self.open_button.configure(state="disabled")
            self.view_button.configure(state="disabled")
            self.today_button.configure(state="disabled", text="Today")
            return
        today = self._today()
        self._note_task = task.id
        self.detail_title.set(task.name)
        lines = [
            f"Project:  {task.project}",
            f"Stage:  {task.stage}",
            f"Deadline:  {task.deadline or 'none'}"
            + (f"  ({insights.deadline_text(task, today)})" if task.deadline and not task.done else ""),
        ]
        idle = insights.idle_days(task, today)
        if idle is not None:
            lines.append("Last change:  " + ("today" if idle == 0 else f"{idle} day(s) ago"))
        if task.starred:
            lines.append("High priority in Odoo")
        self.detail_meta.set("\n".join(lines))
        self.note_text.insert("1.0", self.store.note(task.id))
        self.pin_button.configure(state="normal", text="Unpin" if task.id in self.store.pins else "Pin")
        self.open_button.configure(state="normal")
        self.view_button.configure(state="normal")
        self.today_button.configure(
            state="normal", text="In today" if self.store.in_plan(today, task.id) else "Today"
        )

    def _save_note(self):
        if self._note_task is None:
            return
        text = self.note_text.get("1.0", "end").strip()
        had = bool(self.store.note(self._note_task))
        self.store.set_note(self._note_task, text)
        iid = str(self._note_task)
        if had != bool(text) and self.focus_tree.exists(iid):
            task = self._task(self._note_task)
            self.focus_tree.item(iid, text=self._marks(task, bool(text)) + task.name)

    def toggle_pin(self):
        task_id = self.selected_task_id()
        if task_id is None:
            return
        self.store.toggle_pin(task_id)
        self.render_focus()
        if self.focus_tree.exists(str(task_id)):
            self.focus_tree.see(str(task_id))

    def task_url(self, task_id):
        return f"{self.session.base_url}/web#id={int(task_id)}&model=project.task&view_type=form"

    def open_selected_task(self):
        task_id = self.selected_task_id()
        if task_id is not None:
            webbrowser.open(self.task_url(task_id))

    def _view_change(self):
        selection = self.changes_tree.selection()
        if selection:
            self.open_task(int(selection[0].split(":", 1)[1]))

    def view_selected_task(self):
        task_id = self.selected_task_id()
        if task_id is not None:
            self.open_task(task_id)

    # Task page ----------------------------------------------------------

    def _build_task_page(self, page):
        head = tk.Frame(page, bg=theme.BG)
        head.pack(fill="x", pady=(0, 18))
        PillButton(
            head, "Back", self.close_task, kind="secondary", outer=theme.BG, icon=theme.ICONS["back"]
        ).pack(side="left", anchor="n", pady=(4, 0))
        PillButton(
            head, "Open in Odoo", self._open_detail_in_odoo, kind="primary", outer=theme.BG, icon=theme.ICONS["open"]
        ).pack(side="right", anchor="n", pady=(4, 0))
        self.task_pin_button = PillButton(
            head, "Unpin", self._toggle_detail_pin, kind="secondary", outer=theme.BG, icon=theme.ICONS["pin"]
        )
        self.task_pin_button.pack(side="right", anchor="n", padx=(0, 8), pady=(4, 0))
        titles = tk.Frame(head, bg=theme.BG)
        titles.pack(side="left", fill="x", expand=True, padx=(16, 16))
        self.task_title = tk.StringVar()
        self.task_title_label = ttk.Label(
            titles, textvariable=self.task_title, style="PageTitle.TLabel", wraplength=560, justify="left"
        )
        self.task_title_label.pack(anchor="w")
        titles.bind("<Configure>", lambda e: self.task_title_label.configure(wraplength=max(e.width - 10, 200)))
        self.task_sub = tk.StringVar()
        ttk.Label(titles, textvariable=self.task_sub, style="PageSub.TLabel").pack(anchor="w", pady=(2, 0))

        body = tk.Frame(page, bg=theme.BG)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        card = Card(body, padding=(6, 6))
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        box = ttk.Frame(card.body)
        box.pack(fill="both", expand=True)
        text = tk.Text(
            box,
            wrap="word",
            relief="flat",
            bd=0,
            highlightthickness=0,
            width=20,
            height=5,
            font=(theme.FONT, 10),
            fg=theme.TEXT,
            bg=theme.SURFACE,
            selectbackground=theme.ACCENT,
            selectforeground=theme.ON_ACCENT,
            padx=18,
            pady=12,
            spacing3=7,
            cursor="arrow",
            state="disabled",
        )
        text.pack(side="left", fill="both", expand=True)
        theme.attach_scrollbar(text, box)
        self._build_reply_box(card.body)
        text.tag_configure("link", foreground=theme.ACCENT, lmargin1=14, lmargin2=30)
        text.tag_configure("section", font=(theme.FONT_BOLD, 8), foreground=theme.FAINT, spacing1=18, spacing3=10)
        text.tag_configure("first", spacing1=2)
        text.tag_configure("h", font=(theme.FONT_BOLD, 11), spacing1=8)
        text.tag_configure("li", lmargin1=14, lmargin2=32)
        text.tag_configure("done", lmargin1=14, lmargin2=32, foreground=theme.FAINT, overstrike=True)
        text.tag_configure("pre", font=("Consolas", 10), background=theme.SURFACE_2, lmargin1=10, lmargin2=10)
        text.tag_configure("muted", foreground=theme.MUTED)
        text.tag_configure("author", font=(theme.FONT_BOLD, 10), spacing1=10, spacing3=1)
        text.tag_configure("when", foreground=theme.FAINT, font=(theme.FONT, 9), spacing3=3)
        text.tag_configure("msg", lmargin1=14, lmargin2=14, foreground=theme.MUTED)
        text.tag_configure("error", foreground=theme.DANGER)
        self.task_text = text

        side = Card(body, padding=(18, 16), fixed_width=296)
        side.grid(row=0, column=1, sticky="nsew")
        self.task_props = tk.Frame(side.body, bg=theme.SURFACE)
        self.task_props.pack(fill="x")
        tk.Frame(side.body, bg=theme.BORDER, height=1).pack(fill="x", pady=(4, 12))
        self._build_task_actions(side.body)
        tk.Frame(side.body, bg=theme.BORDER, height=1).pack(fill="x", pady=(12, 12))
        ttk.Label(side.body, text="MY PRIVATE NOTE (NOT SENT TO ODOO)", style="Caps.TLabel").pack(anchor="w")
        self.task_note = tk.Text(
            side.body,
            height=4,
            width=10,
            wrap="word",
            relief="flat",
            font=(theme.FONT, 10),
            fg=theme.TEXT,
            bg=theme.SURFACE_2,
            insertbackground=theme.TEXT,
            selectbackground=theme.ACCENT,
            selectforeground=theme.ON_ACCENT,
            highlightthickness=1,
            highlightbackground=theme.BORDER,
            highlightcolor=theme.ACCENT,
            padx=10,
            pady=8,
        )
        self.task_note.pack(fill="both", expand=True, pady=(6, 0))
        self.task_note.bind("<FocusOut>", lambda _e: self._save_task_note())

    def open_task(self, task_id):
        """Show the in-app task page and load the task from Odoo."""
        self._save_note()
        self._save_task_note()
        if self.current_tab != "task":
            self._task_back = self.current_tab
        self._detail_id = int(task_id)
        self.detail = None
        known = self._task(self._detail_id)
        self.task_title.set(known.name if known else "Task")
        self.task_sub.set("Loading the full task from Odoo…")
        self._fill_task_text([("muted", "Loading…")])
        self._render_props([])
        self._load_task_note()
        self.show_tab("task")
        threading.Thread(target=self._detail_worker, args=(self._detail_id,), daemon=True).start()

    def _detail_worker(self, task_id):
        try:
            self.results.put(("detail", self.client.fetch_task_detail(self.session, task_id)))
        except OdooConnectionError as exc:
            self.results.put(("detail_error", (task_id, str(exc))))
        except Exception:
            self.results.put(("detail_error", (task_id, "Could not load this task from Odoo.")))

    def open_task_sync(self, task_id):
        if self.current_tab != "task":
            self._task_back = self.current_tab
        self._detail_id = int(task_id)
        self._load_task_note()
        self._render_task(self.client.fetch_task_detail(self.session, task_id))
        self.show_tab("task")

    def close_task(self):
        self._save_task_note()
        self._detail_id = None
        self.detail = None
        self.render_focus()
        self.show_tab(self._task_back)

    def _fill_task_text(self, lines):
        text = self.task_text
        text.configure(state="normal")
        text.delete("1.0", "end")
        for index, (tags, content) in enumerate(lines):
            tags = (tags,) if isinstance(tags, str) else tuple(tags)
            if index == 0:
                tags += ("first",)
            text.insert("end", content + "\n", tags)
            for tag in tags:
                if tag.startswith("att:"):
                    attachment_id = int(tag[4:])
                    text.tag_bind(tag, "<Button-1>", lambda _e, i=attachment_id: self.open_attachment(i))
                    text.tag_bind(tag, "<Enter>", lambda _e: text.configure(cursor="hand2"))
                    text.tag_bind(tag, "<Leave>", lambda _e: text.configure(cursor="arrow"))
                    text.mark_set(f"img:{attachment_id}", "end-1c")
                    text.mark_gravity(f"img:{attachment_id}", "left")
        text.configure(state="disabled")
        text.yview_moveto(0)

    def task_lines(self, detail):
        """(tag, text) lines for the task page body."""
        prefixes = {"li": "•  ", "check": "☐  ", "done": "☑  "}
        lines = [("section", "DESCRIPTION")]
        blocks = richtext.html_to_blocks(detail.description)
        if not blocks:
            lines.append(("muted", "No description."))
        for kind, content in blocks:
            tag = {"ol": "li", "check": "li"}.get(kind, kind)
            lines.append((tag, prefixes.get(kind, "") + content))
        if detail.subtasks:
            closed = sum(1 for sub in detail.subtasks if sub.closed)
            lines.append(("section", f"SUB-TASKS   {closed}/{len(detail.subtasks)} DONE"))
            for sub in detail.subtasks:
                mark = "☑  " if sub.closed else "☐  "
                lines.append(("done" if sub.closed else "li", f"{mark}{sub.name}   ·   {sub.stage}"))
        if detail.attachments:
            lines.append(("section", f"ATTACHMENTS   {len(detail.attachments)}"))
            for attachment in detail.attachments:
                lines.append(
                    (("link", f"att:{attachment.id}"), f"{attachment.name}   ·   {human_size(attachment.size)}")
                )
        lines.append(("section", f"MESSAGES   {len(detail.messages)}"))
        if not detail.messages:
            lines.append(("muted", "No messages on this task."))
        for message in detail.messages:
            lines.append(("author", message.author))
            lines.append(("when", message.date))
            lines.append(("msg", richtext.html_to_text(message.body) or "(no text)"))
        return lines

    def task_properties(self, detail):
        deadline = detail.deadline
        if detail.deadline and not detail.done:
            deadline = f"{detail.deadline}  ({insights.deadline_text(detail, self._today())})"
        hours = ""
        if detail.allocated_hours or detail.spent_hours:
            hours = f"{detail.spent_hours:g} h spent"
            if detail.allocated_hours:
                hours += f" of {detail.allocated_hours:g} h"
        rows = [
            ("Stage", detail.stage + (f"  ·  {detail.state}" if detail.state else "")),
            ("Deadline", deadline or "None"),
            ("Assigned to", ", ".join(detail.assignees)),
            ("Tags", ", ".join(detail.tags)),
            ("Priority", "High" if detail.starred else ""),
            ("Customer", detail.customer),
            ("Milestone", detail.milestone),
            ("Parent task", detail.parent),
            ("Hours", hours),
            ("Created", detail.created),
            ("Last change", detail.updated),
        ]
        return [(label, value) for label, value in rows if value]

    def _render_props(self, rows):
        for child in self.task_props.winfo_children():
            child.destroy()
        self.task_props.columnconfigure(1, weight=1)
        for index, (label, value) in enumerate(rows):
            ttk.Label(self.task_props, text=label, style="Sub.TLabel").grid(
                row=index, column=0, sticky="nw", padx=(0, 12), pady=(0, 9)
            )
            ttk.Label(self.task_props, text=value, wraplength=160, justify="left").grid(
                row=index, column=1, sticky="nw", pady=(0, 9)
            )

    def _render_task(self, detail):
        self.detail = detail
        self.task_title.set(detail.name)
        self.task_sub.set(f"{detail.project}  ·  {detail.stage}  ·  #{detail.id}")
        self._fill_task_text(self.task_lines(detail))
        self._render_props(self.task_properties(detail))
        self.task_pin_button.configure(text="Unpin" if detail.id in self.store.pins else "Pin")
        self._task_images = []
        self._refresh_task_actions()
        self._load_inline_images(detail)

    def _load_task_note(self):
        self.task_note.delete("1.0", "end")
        if self._detail_id is not None:
            self.task_note.insert("1.0", self.store.note(self._detail_id))
            self.task_pin_button.configure(text="Unpin" if self._detail_id in self.store.pins else "Pin")

    def _save_task_note(self):
        if self._detail_id is not None and self.task_note.winfo_exists():
            self.store.set_note(self._detail_id, self.task_note.get("1.0", "end").strip())

    def _toggle_detail_pin(self):
        if self._detail_id is not None:
            pinned = self.store.toggle_pin(self._detail_id)
            self.task_pin_button.configure(text="Unpin" if pinned else "Pin")

    def _open_detail_in_odoo(self):
        if self._detail_id is not None:
            webbrowser.open(self.task_url(self._detail_id))

    # Export -------------------------------------------------------------

    def export_rows(self):
        today = self._today()
        rows = [["Urgency", "Task", "Project", "Stage", "Deadline", "Days untouched", "Pinned", "Private note", "Link"]]
        labels = dict(insights.BUCKETS)
        order = [key for key, _label in insights.BUCKETS]
        ordered = sorted(
            self.visible_tasks, key=lambda t: (order.index(insights.bucket_of(t, today)), t.deadline or "9999")
        )
        for task in ordered:
            idle = insights.idle_days(task, today)
            rows.append(
                [
                    labels[insights.bucket_of(task, today)],
                    task.name,
                    task.project,
                    task.stage,
                    task.deadline,
                    "" if idle is None else idle,
                    "yes" if task.id in self.store.pins else "",
                    self.store.note(task.id),
                    self.task_url(task.id),
                ]
            )
        return rows

    def export_csv(self):
        if not self.visible_tasks:
            self._set_status("Nothing to export.", error=True)
            return
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export tasks",
            defaultextension=".csv",
            initialfile=f"my-odoo-tasks-{self._today().isoformat()}.csv",
            filetypes=[("CSV file", "*.csv")],
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as handle:
                csv.writer(handle).writerows(self.export_rows())
        except OSError:
            self._set_status("Could not save the file. Is it open in Excel?", error=True)
            return
        self._set_status(f"Exported {len(self.visible_tasks)} task(s) to {path}")

    # Misc ---------------------------------------------------------------

    def _sign_out(self):
        self._save_note()
        self._save_task_note()
        self.on_sign_out()

    def destroy(self):
        self._save_note()
        self._save_task_note()
        self._alive = False
        self.health_canvas.unbind_all("<MouseWheel>")
        self.frame.destroy()

    def _set_status(self, text, error=False):
        self.status_var.set(text)
        self.status_label.configure(fg=theme.DANGER if error else theme.FAINT)
