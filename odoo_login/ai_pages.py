"""The AI assistant in the interface: its Settings section, the "Ask AI" menus and the answer box.

Mixed into DashboardView. Nothing is sent to a provider unless the user picks
an AI action, and the first one of a session asks before sending.
"""

import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

from . import ai, insights, richtext, settings, theme
from .theme import PillButton

LANGUAGES = ("English", "French", "Indonesian")


class AIMixin:
    def _init_ai_state(self):
        self._ai_agreed = set()  # providers the user agreed to send text to, this session
        self._ai_models = {}  # provider -> model ids loaded from the provider

    # Choice of provider and model -------------------------------------------------

    def ai_provider(self):
        provider = settings.load().get("ai_provider", "claude")
        return provider if provider in ai.PROVIDERS else "claude"

    def ai_model(self):
        model = settings.load().get("ai_model", "")
        return model or (ai.DEFAULT_CLAUDE_MODEL if self.ai_provider() == "claude" else "")

    def _ask_provider(self, system, prompt):
        """Seam for tests: one question to the chosen provider."""
        provider = self.ai_provider()
        return ai.ask(provider, ai.load_key(provider), self.ai_model(), system, prompt)

    # Running a request ------------------------------------------------------------------

    def ai_run(self, title, prompt, what, on_use=None, use_label=""):
        """Ask the provider in the background and show the answer. `what` names the text being sent."""
        provider = self.ai_provider()
        name = ai.provider_name(provider)
        if not self.ai_ready():
            self._error("The AI assistant is not set up yet.\n\nOpen Settings, choose a provider and save your API key.")
            return False
        if provider not in self._ai_agreed:
            if not self._confirm(
                f"Send to {name}",
                f"{what} will be sent to {name} to write the answer. It leaves this computer and your Odoo.\n\n"
                "Continue? You are asked once each time you start the app.",
            ):
                return False
            self._ai_agreed.add(provider)
        self._set_status(f"Asking {name}…")

        def run():
            try:
                self.results.put(("ai_done", (title, self._ask_provider(ai.SYSTEM, prompt), on_use, use_label)))
            except ai.AIError as exc:
                self.results.put(("ai_error", str(exc)))
            except Exception:
                self.results.put(("ai_error", f"{name} could not answer. Try again."))

        threading.Thread(target=run, daemon=True).start()
        return True

    def ai_ready(self):
        provider = self.ai_provider()
        return bool(ai.load_key(provider)) and bool(self.ai_model())

    def _handle_ai_result(self, kind, payload):
        if kind == "ai_done":
            title, text, on_use, use_label = payload
            self._set_status("Answer received.")
            self._show_ai_answer(title, text, on_use, use_label)
        elif kind == "ai_error":
            self._set_status(payload, error=True)
            self._error(payload)
        elif kind == "ai_models":
            provider, models = payload
            self._ai_models[provider] = models
            self._ai_models_loaded(provider)
        elif kind == "ai_settings_error":
            self.ai_status.set(payload)
        elif kind == "ai_test":
            self.ai_status.set(payload)
        else:
            return False
        return True

    def _show_ai_answer(self, title, text, on_use=None, use_label=""):
        window = tk.Toplevel(self.root)
        window.title(f"{title}  ·  {ai.provider_name(self.ai_provider())}")
        window.configure(bg=theme.SURFACE)
        window.transient(self.root)
        theme.set_title_bar(window)
        body = tk.Frame(window, bg=theme.SURFACE)
        body.pack(fill="both", expand=True, padx=18, pady=16)
        tk.Label(
            body, text=f"Written by {self.ai_model()}. Read it before you use it: it can be wrong.", bg=theme.SURFACE,
            fg=theme.FAINT, font=(theme.FONT, 9),
        ).pack(anchor="w", pady=(0, 8))  # fmt: skip
        buttons = tk.Frame(body, bg=theme.SURFACE)
        buttons.pack(side="bottom", fill="x", pady=(12, 0))
        box = tk.Frame(body, bg=theme.SURFACE)
        box.pack(fill="both", expand=True)
        answer = tk.Text(
            box, wrap="word", width=78, height=20, relief="flat", font=(theme.FONT, 10), fg=theme.TEXT,
            bg=theme.SURFACE_2, insertbackground=theme.TEXT, highlightthickness=1, highlightbackground=theme.BORDER,
            highlightcolor=theme.ACCENT, padx=12, pady=10,
        )  # fmt: skip
        answer.insert("1.0", text)
        answer.pack(side="left", fill="both", expand=True)
        theme.attach_scrollbar(answer, box)

        def current():
            return answer.get("1.0", "end").strip()

        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(current())
            self._set_status("Copied.")

        def use():
            on_use(current())
            window.destroy()

        PillButton(buttons, "Close", window.destroy, kind="secondary").pack(side="right")
        PillButton(buttons, "Copy", copy, kind="secondary" if on_use else "primary").pack(side="right", padx=(0, 8))
        if on_use:
            PillButton(buttons, use_label or "Use", use, kind="primary").pack(side="right", padx=(0, 8))
        window.bind("<Escape>", lambda _e: window.destroy())
        window.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - window.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - window.winfo_height()) // 3
        window.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self._ai_window = window

    def _ai_menu(self, button, items):
        menu = tk.Menu(
            self.root, tearoff=0, bg=theme.SURFACE_2, fg=theme.TEXT, activebackground=theme.ACCENT_SOFT,
            activeforeground=theme.TEXT, relief="flat", font=(theme.FONT, 10),
        )  # fmt: skip
        for label, command in items:
            if label is None:
                menu.add_separator()
            else:
                menu.add_command(label=label, command=command)
        menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height() + 2)

    # What can be asked ---------------------------------------------------------------------

    def _messages_text(self, messages):
        return "\n".join(
            f"[{message.date}] {message.author}: {' '.join(richtext.html_to_text(message.body).split())}"
            for message in messages
        )

    def task_material(self):
        detail = self.detail
        return ai.task_material(detail, richtext.html_to_text(detail.description), self._messages_text(detail.messages))

    def tasks_text(self):
        today = self._today()
        lines = []
        for task in self.open_tasks:
            idle = insights.idle_days(task, today)
            lines.append(
                f"{task.name} | {task.project} | {task.stage} | {task.deadline or 'no deadline'} | "
                f"{'?' if idle is None else idle}"
            )
        return "\n".join(lines) or "(no open task)"

    def task_ai_clicked(self):
        if not self.detail:
            return
        items = [("Summarise this task", self.ai_summarise_task), ("Draft a reply", self.ai_draft_task_reply), (None, None)]
        items += [(f"Translate to {language}", lambda l=language: self.ai_translate_task(l)) for language in LANGUAGES]
        self._ai_menu(self.task_ai_button, items)

    def ai_summarise_task(self):
        what = f"The description and messages of “{self.detail.name}”"
        self.ai_run("Summary", ai.prompt_summarise_task(self.task_material(), self.email), what)

    def ai_draft_task_reply(self):
        def use(text):
            self.reply_text.delete("1.0", "end")
            self.reply_text.insert("1.0", text)

        what = f"The description and messages of “{self.detail.name}”"
        self.ai_run("Draft reply", ai.prompt_reply(self.task_material(), self.email), what, use, "Put in reply box")

    def ai_translate_task(self, language):
        what = f"The description and messages of “{self.detail.name}”"
        self.ai_run(f"In {language}", ai.prompt_translate(self.task_material(), language), what)

    def discuss_ai_clicked(self):
        conversation = self._conversation()
        if conversation is None or not self.channel_messages:
            self.discuss_sub.set("Open a conversation first.")
            return
        items = [("Summarise this conversation", self.ai_summarise_channel), ("Draft a reply", self.ai_draft_channel_reply), (None, None)]
        items += [(f"Translate to {language}", lambda l=language: self.ai_translate_channel(l)) for language in LANGUAGES]
        self._ai_menu(self.discuss_ai_button, items)

    def _channel_what(self):
        return f"The last {len(self.channel_messages)} messages of the conversation with {self._conversation().name}"

    def ai_summarise_channel(self):
        text = self._messages_text(self.channel_messages)
        prompt = ai.prompt_summarise_conversation(self._conversation().name, text, self.email)
        self.ai_run("Summary", prompt, self._channel_what())

    def ai_draft_channel_reply(self):
        def use(text):
            self.channel_reply.delete("1.0", "end")
            self.channel_reply.insert("1.0", text)

        material = f"Conversation with {self._conversation().name}, oldest first:\n{self._messages_text(self.channel_messages)}"
        self.ai_run("Draft reply", ai.prompt_reply(material, self.email), self._channel_what(), use, "Put in reply box")

    def ai_translate_channel(self, language):
        text = self._messages_text(self.channel_messages)
        self.ai_run(f"In {language}", ai.prompt_translate(text, language), self._channel_what())

    def ai_plan_day(self):
        extras = []
        urgent = [row for row in self.activities if row.tag in ("overdue", "today")]
        if urgent:
            extras.append("Activities due: " + "; ".join(f"{row.cells[2] or row.cells[1]} on {row.cells[3]}" for row in urgent))
        if self.today_meetings:
            extras.append("Meetings today: " + "; ".join(f"{m.time or 'all day'} {m.name}" for m in self.today_meetings))
        prompt = ai.prompt_plan_day(self.tasks_text(), "\n".join(extras), self._today().isoformat())
        self.ai_run("Plan for today", prompt, f"The titles, projects, stages and deadlines of your {len(self.open_tasks)} open tasks")

    def ai_weekly_report(self):
        self.ai_run("Weekly report", ai.prompt_weekly_report(self.summary_text()), "Your weekly summary (task titles and projects)")

    def ai_question_clicked(self):
        answer = self._form(
            "Ask about my tasks", [("question", "Your question", "text", "", None)], ok_text="Ask"
        )
        if answer and answer["question"]:
            prompt = ai.prompt_question(answer["question"], self.tasks_text(), self._today().isoformat())
            what = f"Your question and the titles, projects, stages and deadlines of your {len(self.open_tasks)} open tasks"
            self.ai_run("Answer", prompt, what)

    # Settings section ---------------------------------------------------------------------------

    def _build_ai_settings(self, body, section):
        section("AI ASSISTANT")
        tk.Label(
            body, bg=theme.SURFACE, fg=theme.FAINT, font=(theme.FONT, 9), justify="left",
            text="Summaries, reply drafts, translations and a daily plan, written by the provider you choose with your "
            "own API key.\nText is sent only when you click an AI action. The key is kept in Windows Credential Manager.",
        ).pack(anchor="w", pady=(0, 8))  # fmt: skip
        grid = tk.Frame(body, bg=theme.SURFACE)
        grid.pack(anchor="w")

        def label(row, text):
            tk.Label(grid, text=text, bg=theme.SURFACE, fg=theme.MUTED, font=(theme.FONT, 9)).grid(
                row=row, column=0, sticky="w", padx=(0, 14), pady=4
            )

        label(0, "Provider")
        self.ai_provider_var = tk.StringVar(value=ai.provider_name(self.ai_provider()))
        box = ttk.Combobox(
            grid, textvariable=self.ai_provider_var, values=list(ai.PROVIDERS.values()), state="readonly", width=30,
            font=(theme.FONT, 10),
        )  # fmt: skip
        box.grid(row=0, column=1, sticky="w", pady=4, ipady=2)
        box.bind("<<ComboboxSelected>>", lambda _e: self._ai_provider_changed())

        label(1, "API key")
        self.ai_key_var = tk.StringVar()
        ttk.Entry(grid, textvariable=self.ai_key_var, show="•", width=32, font=(theme.FONT, 10)).grid(
            row=1, column=1, sticky="w", pady=4
        )
        keys = tk.Frame(grid, bg=theme.SURFACE)
        keys.grid(row=1, column=2, sticky="w", padx=(10, 0))
        PillButton(keys, "Save key", self._ai_save_key, kind="secondary", height=32).pack(side="left")
        PillButton(keys, "Remove", self._ai_remove_key, kind="ghost", height=32).pack(side="left", padx=(6, 0))
        PillButton(keys, "Get a key", self._ai_open_key_page, kind="ghost", height=32).pack(side="left", padx=(6, 0))

        label(2, "Model")
        self.ai_model_var = tk.StringVar(value=self.ai_model())
        self.ai_model_box = ttk.Combobox(
            grid, textvariable=self.ai_model_var, values=self._ai_models.get(self.ai_provider(), []), width=30,
            font=(theme.FONT, 10),
        )  # fmt: skip
        self.ai_model_box.grid(row=2, column=1, sticky="w", pady=4, ipady=2)
        self.ai_model_box.bind("<<ComboboxSelected>>", lambda _e: self._ai_model_changed())
        self.ai_model_box.bind("<FocusOut>", lambda _e: self._ai_model_changed())
        models = tk.Frame(grid, bg=theme.SURFACE)
        models.grid(row=2, column=2, sticky="w", padx=(10, 0))
        PillButton(models, "Load models", self._ai_load_models, kind="secondary", height=32).pack(side="left")
        PillButton(models, "Test", self._ai_test, kind="secondary", height=32).pack(side="left", padx=(6, 0))

        self.ai_status = tk.StringVar()
        tk.Label(body, textvariable=self.ai_status, bg=theme.SURFACE, fg=theme.MUTED, font=(theme.FONT, 9)).pack(
            anchor="w", pady=(4, 0)
        )
        self._ai_show_key_state()

    def _ai_selected_provider(self):
        chosen = self.ai_provider_var.get()
        return next((key for key, name in ai.PROVIDERS.items() if name == chosen), "claude")

    def _ai_show_key_state(self):
        provider = self.ai_provider()
        saved = bool(ai.load_key(provider))
        self.ai_status.set(
            f"A key is saved for {ai.provider_name(provider)}." if saved else f"No key saved for {ai.provider_name(provider)} yet."
        )

    def _ai_provider_changed(self):
        provider = self._ai_selected_provider()
        if provider != self.ai_provider():
            default = ai.DEFAULT_CLAUDE_MODEL if provider == "claude" else ""
            settings.save({"ai_provider": provider, "ai_model": default})
            self.ai_model_var.set(default)
        self.ai_model_box.configure(values=self._ai_models.get(provider, []))
        self.ai_key_var.set("")
        self._ai_show_key_state()

    def _ai_model_changed(self):
        settings.save({"ai_model": self.ai_model_var.get().strip()})

    def _ai_save_key(self):
        key = self.ai_key_var.get().strip()
        provider = self.ai_provider()
        if not key:
            self.ai_status.set("Paste the API key first.")
            return
        self.ai_key_var.set("")
        if ai.save_key(provider, key):
            self.ai_status.set("Key saved in Windows Credential Manager. Loading the models…")
            self._ai_load_models()
        else:
            self.ai_status.set("Windows could not save the key.")

    def _ai_remove_key(self):
        ai.delete_key(self.ai_provider())
        self.ai_key_var.set("")
        self._ai_show_key_state()

    def _ai_open_key_page(self):
        webbrowser.open(ai.KEY_PAGES[self.ai_provider()])

    def _ai_load_models(self):
        provider = self.ai_provider()
        key = ai.load_key(provider)
        if not key:
            self.ai_status.set("Save an API key first.")
            return
        self.ai_status.set("Loading the models…")

        def run():
            try:
                self.results.put(("ai_models", (provider, ai.list_models(provider, key))))
            except ai.AIError as exc:
                self.results.put(("ai_settings_error", str(exc)))
            except Exception:
                self.results.put(("ai_settings_error", "Could not load the models."))

        threading.Thread(target=run, daemon=True).start()

    def _ai_models_loaded(self, provider):
        if provider != self.ai_provider() or not hasattr(self, "ai_model_box") or not self.ai_model_box.winfo_exists():
            return
        models = self._ai_models.get(provider, [])
        self.ai_model_box.configure(values=models)
        if models and self.ai_model_var.get() not in models:
            self.ai_model_var.set(ai.DEFAULT_CLAUDE_MODEL if ai.DEFAULT_CLAUDE_MODEL in models else models[0])
            self._ai_model_changed()
        self.ai_status.set(f"{len(models)} model(s) available. Selected: {self.ai_model_var.get()}." if models else "No model found for this key.")

    def _ai_test(self):
        self._ai_model_changed()
        if not self.ai_ready():
            self.ai_status.set("Save a key and choose a model first.")
            return
        name = ai.provider_name(self.ai_provider())
        self.ai_status.set(f"Testing {name}…")

        def run():
            try:
                self._ask_provider("You are a connection test.", "Reply with the single word OK.")
                self.results.put(("ai_test", f"{name} answered. The assistant is ready."))
            except ai.AIError as exc:
                self.results.put(("ai_test", str(exc)))
            except Exception:
                self.results.put(("ai_test", f"{name} could not be reached."))

        threading.Thread(target=run, daemon=True).start()
