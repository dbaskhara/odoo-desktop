import queue
import threading
import tkinter as tk
from tkinter import ttk

from . import settings, theme
from .dashboard import DashboardView
from .odoo_client import OdooClient, OdooConnectionError, OdooSession
from .theme import Card, PillButton

APP_NAME = "Odoo Desktop"
APP_VERSION = "3.3"
LOGIN_SIZE = "1000x760"
LOGIN_MIN = (880, 740)
DASHBOARD_SIZE = "1320x820"
DASHBOARD_MIN = (1200, 700)


class Field:
    """A labelled entry with a real placeholder (never stored in the variable)."""

    def __init__(self, parent, label, variable, placeholder="", secret=False, hint=""):
        self.variable = variable
        self.secret = secret
        self.frame = ttk.Frame(parent)
        self.frame.pack(fill="x", pady=(0, 14))

        top = ttk.Frame(self.frame)
        top.pack(fill="x", pady=(0, 5))
        ttk.Label(top, text=label, style="Field.TLabel").pack(side="left")
        if hint:
            ttk.Label(top, text=hint, style="Hint.TLabel").pack(side="right")

        row = ttk.Frame(self.frame)
        row.pack(fill="x")
        self.entry = ttk.Entry(row, textvariable=variable, show="•" if secret else "", font=(theme.FONT, 10))
        self.entry.pack(side="left", fill="x", expand=True)
        if secret:
            self.toggle = ttk.Button(row, text="Show", style="Link.TButton", command=self._toggle_secret)
            self.toggle.pack(side="right", padx=(6, 0))

        self.placeholder = tk.Label(
            row, text=placeholder, bg=theme.SURFACE_2, fg=theme.FAINT, font=(theme.FONT, 10), cursor="xterm"
        )
        self.placeholder.bind("<Button-1>", lambda _e: self.entry.focus_set())
        self._trace = variable.trace_add("write", lambda *_: self._refresh_placeholder())
        self.entry.bind("<Configure>", lambda _e: self._refresh_placeholder(), add="+")
        self.entry.bind("<Destroy>", lambda _e: variable.trace_remove("write", self._trace), add="+")
        self._refresh_placeholder()

    def _refresh_placeholder(self):
        if not self.placeholder.winfo_exists():
            return
        if self.variable.get() or not self.placeholder.cget("text"):
            self.placeholder.place_forget()
        else:
            self.placeholder.place(in_=self.entry, x=11, rely=0.5, anchor="w")

    def _toggle_secret(self):
        hidden = self.entry.cget("show") != ""
        self.entry.configure(show="" if hidden else "•")
        self.toggle.configure(text="Hide" if hidden else "Show")


class LoginWindow:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry(LOGIN_SIZE)
        self.root.minsize(*LOGIN_MIN)
        saved = settings.load()
        theme.set_mode(saved.get("theme", "dark"))
        theme.apply_styles(root)
        theme.set_title_bar(root)
        self.client = OdooClient()
        self.results = queue.Queue()
        self._active = True
        self._pending = None
        self.dashboard = None

        self.url_var = tk.StringVar(value=saved.get("url", ""))
        self.database_var = tk.StringVar(value=saved.get("database", ""))
        self.email_var = tk.StringVar(value=saved.get("email", ""))
        self.api_key_var = tk.StringVar()
        self.remember_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar()
        self._status_kind = "info"

        self._build()
        self.root.bind("<Return>", self._on_return)
        self.root.after(100, self._poll_result)

    # Layout -----------------------------------------------------------

    def _build(self):
        self.container = tk.Frame(self.root, bg=theme.BG)
        self.container.pack(fill="both", expand=True)
        self._build_brand_panel(self.container)
        self._build_form(self.container)
        self._focus_first_empty()

    def _build_brand_panel(self, parent):
        panel = tk.Canvas(parent, width=360, bg=theme.SIDEBAR, highlightthickness=0)
        panel.pack(side="left", fill="y")
        dark = theme.MODE == "dark"
        top = theme.SIDEBAR
        glow = "#0d3b48" if dark else "#cfeef3"
        title = theme.TEXT
        soft = theme.MUTED

        def blend(a, b, t):
            a = [int(a[i : i + 2], 16) for i in (1, 3, 5)]
            b = [int(b[i : i + 2], 16) for i in (1, 3, 5)]
            return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))

        def draw(event):
            panel.delete("all")
            steps = 60
            for index in range(steps):
                y0 = event.height * index / steps
                y1 = event.height * (index + 1) / steps + 1
                t = max(0.0, (index / steps - 0.35) / 0.65) ** 1.6
                panel.create_rectangle(0, y0, event.width, y1, fill=blend(top, glow, t), outline="")
            x = 40
            theme.round_rect(panel, x, 46, x + 48, 94, 14, fill=theme.DEEP, outline=theme.DEEP)
            panel.create_oval(x + 13, 59, x + 35, 81, outline="#ffffff", width=4)
            panel.create_text(x, 128, text=APP_NAME, fill=title, font=(theme.FONT_BOLD, 24), anchor="w")
            panel.create_text(
                x, 166, text="A calm home for your Odoo work.", fill=soft, font=(theme.FONT, 11), anchor="w"
            )
            y = 226
            for color, text in (
                (theme.ACCENT, "One focus list across all projects"),
                (theme.PURPLE, "See what changed since your last visit"),
                (theme.WARN, "Pin tasks and keep private notes"),
                (theme.SUCCESS, "Project health at a glance"),
            ):
                panel.create_oval(x, y - 4, x + 8, y + 4, fill=color, outline="")
                panel.create_text(x + 22, y, text=text, fill=title, font=(theme.FONT, 10), anchor="w")
                y += 32
            panel.create_text(
                x, event.height - 36, text=f"Version {APP_VERSION}", fill=soft, font=(theme.FONT, 9), anchor="w"
            )

        panel.bind("<Configure>", draw)
        tk.Frame(parent, bg=theme.BORDER, width=1).pack(side="left", fill="y")

    def _build_form(self, parent):
        area = tk.Frame(parent, bg=theme.BG)
        area.pack(side="left", fill="both", expand=True)
        PillButton(
            area, "", self.toggle_theme, kind="ghost", outer=theme.BG,
            icon=theme.ICONS["sun" if theme.MODE == "dark" else "moon"], width=38, height=36,
        ).place(relx=1.0, x=-18, y=16, anchor="ne")  # fmt: skip

        card = Card(area, padding=(34, 30), radius=18)
        card.place(relx=0.5, rely=0.5, anchor="center")
        form = card.body
        tk.Frame(form, bg=theme.SURFACE, width=400, height=1).pack()

        ttk.Label(form, text="Sign in", style="Title.TLabel").pack(anchor="w")
        ttk.Label(form, text="Use your Odoo password or an API key.", style="Sub.TLabel").pack(
            anchor="w", pady=(4, 22)
        )

        self.url_field = Field(form, "Server", self.url_var, "your-company.odoo.com")
        self.database_field = Field(
            form, "Database", self.database_var, "Leave empty to detect it", hint="optional"
        )
        self.email_field = Field(form, "Email", self.email_var, "you@company.com")
        self.api_key_field = Field(
            form, "Password or API key", self.api_key_var, "Your Odoo password, or an API key", secret=True
        )

        options = ttk.Frame(form)
        options.pack(fill="x", pady=(0, 14))
        ttk.Checkbutton(
            options, text="Remember server, database and email", variable=self.remember_var
        ).pack(side="left")
        ttk.Button(options, text="Password or API key?", style="Link.TButton", command=self._toggle_help).pack(
            side="right"
        )

        self.help_label = ttk.Label(
            form,
            text="Your normal Odoo password works. If your account uses two-step "
            "verification, Odoo only accepts an API key here: in Odoo, click your avatar > "
            "My Profile or Preferences > Account Security > New API Key.",
            style="Sub.TLabel",
            wraplength=395,
            justify="left",
        )

        self.banner = tk.Frame(form, bg=theme.INFO_BG)
        self.status_label = tk.Label(
            self.banner,
            textvariable=self.status_var,
            bg=theme.INFO_BG,
            fg=theme.TEXT,
            font=(theme.FONT, 9),
            wraplength=370,
            justify="left",
            anchor="w",
        )
        self.status_label.pack(fill="x", padx=12, pady=9)

        self.login_button = PillButton(form, "Sign in", self.sign_in, kind="primary", height=42)
        self.login_button.pack(fill="x")
        ttk.Label(form, text="Your password or API key is never saved on this computer.", style="Hint.TLabel").pack(
            anchor="center", pady=(12, 0)
        )
        if self.status_var.get():
            self._set_status(self.status_var.get(), kind=self._status_kind)

    def toggle_theme(self):
        mode = theme.set_mode("light" if theme.MODE == "dark" else "dark")
        settings.save({"theme": mode})
        theme.apply_styles(self.root)
        theme.set_title_bar(self.root)
        self.container.destroy()
        self._build()

    def _toggle_help(self):
        if self.help_label.winfo_ismapped():
            self.help_label.pack_forget()
        else:
            self.help_label.pack(fill="x", pady=(0, 14), before=self.login_button)

    def _focus_first_empty(self):
        for field in (self.url_field, self.email_field, self.api_key_field):
            if not field.variable.get():
                field.entry.focus_set()
                return
        self.api_key_field.entry.focus_set()

    # Sign-in ------------------------------------------------------------

    def _on_return(self, _event=None):
        if self._active and str(self.login_button.cget("state")) != "disabled":
            self.sign_in()

    def sign_in(self):
        url = self.url_var.get().strip()
        email = self.email_var.get().strip()
        api_key = self.api_key_var.get()
        database = self.database_var.get().strip()
        missing = [
            name for name, value in (("server", url), ("email", email), ("password or API key", api_key)) if not value
        ]
        if missing:
            self._set_status("Please enter your " + ", ".join(missing) + ".", kind="error")
            return
        if self.remember_var.get():
            settings.save({"url": url, "database": database, "email": email})
        else:
            settings.clear()
        self.login_button.configure(state="disabled", text="Signing in…")
        self._set_status("Connecting to " + url + " …")
        self._pending = (email, api_key)
        threading.Thread(target=self._login_worker, args=(url, email, api_key, database), daemon=True).start()

    def _login_worker(self, url, email, api_key, database):
        try:
            self.results.put((True, self.client.login(url, email, api_key, database)))
        except OdooConnectionError as exc:
            self.results.put((False, str(exc)))
        except Exception:
            self.results.put((False, "Sign-in failed unexpectedly. Check your server settings and try again."))

    def _poll_result(self):
        if not self._active:
            return
        try:
            success, result = self.results.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll_result)
            return
        self.login_button.configure(state="normal", text="Sign in")
        if success:
            self.show_dashboard(result)
            return
        self._set_status(result, kind="error")
        self.root.after(100, self._poll_result)

    def show_dashboard(self, result):
        email, api_key = self._pending or ("", "")
        self._pending = None
        self._active = False
        self.api_key_var.set("")
        session = OdooSession(base_url=result.base_url, database=result.database, uid=result.uid, api_key=api_key)
        self.root.unbind("<Return>")
        self.container.destroy()
        self.root.minsize(*DASHBOARD_MIN)
        self.root.geometry(DASHBOARD_SIZE)
        self.dashboard = DashboardView(self.root, self.client, session, email, on_sign_out=self.sign_out)
        self.dashboard.refresh()
        return self.dashboard

    def sign_out(self):
        if self.dashboard:
            self.dashboard.destroy()
            self.dashboard = None
        for widget in self.root.winfo_children():
            widget.destroy()
        return LoginWindow(self.root)

    def _set_status(self, text, kind="info"):
        colors = {
            "info": (theme.INFO_BG, theme.MUTED),
            "error": (theme.DANGER_BG, theme.DANGER),
        }
        bg, fg = colors[kind]
        self._status_kind = kind
        self.status_var.set(text)
        self.banner.configure(bg=bg)
        self.status_label.configure(bg=bg, fg=fg)
        if text and not self.banner.winfo_ismapped():
            self.banner.pack(fill="x", pady=(0, 14), before=self.login_button)
        elif not text:
            self.banner.pack_forget()


def run():
    theme.enable_dpi_awareness()
    root = tk.Tk()
    theme.set_window_icon(root)
    theme.load_fonts(root)
    LoginWindow(root)
    root.mainloop()
