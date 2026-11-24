"""Colours, fonts, ttk styles and the small custom widgets shared by every screen.

Two palettes (dark is the default). `set_mode()` swaps the module-level colour
names, so screens must be rebuilt after a switch.
"""

import sys
import tkinter as tk
import tkinter.font
from pathlib import Path
from tkinter import ttk

PALETTES = {
    "dark": dict(
        BG="#0b0f14",
        SIDEBAR="#0e131a",
        SURFACE="#141a22",
        SURFACE_2="#1b232d",
        BORDER="#232c38",
        TEXT="#f0f4f7",
        MUTED="#93a0b0",
        FAINT="#5d6a7a",
        ACCENT="#6ee7ea",
        ACCENT_HOVER="#93eef0",
        ON_ACCENT="#06262b",
        ACCENT_SOFT="#16313a",
        DANGER="#f87171",
        DANGER_BG="#2a1618",
        WARN="#f3a93c",
        WARN_BG="#2a2012",
        SUCCESS="#34d399",
        SUCCESS_BG="#11281f",
        PURPLE="#9b7bf5",
        DEEP="#0e7490",
        NEUTRAL="#3a4553",
        INFO_BG="#16212c",
    ),
    "light": dict(
        BG="#f3f5f8",
        SIDEBAR="#fbfcfd",
        SURFACE="#ffffff",
        SURFACE_2="#f0f3f6",
        BORDER="#e1e6ec",
        TEXT="#131a22",
        MUTED="#5f6b7a",
        FAINT="#98a2b0",
        ACCENT="#0e7490",
        ACCENT_HOVER="#0b5f77",
        ON_ACCENT="#ffffff",
        ACCENT_SOFT="#e3f4f7",
        DANGER="#c0352b",
        DANGER_BG="#fdf0ee",
        WARN="#b86e0c",
        WARN_BG="#fdf5e6",
        SUCCESS="#0d8a5f",
        SUCCESS_BG="#e8f8f1",
        PURPLE="#7556d8",
        DEEP="#0e7490",
        NEUTRAL="#cbd3dc",
        INFO_BG="#eef3f8",
    ),
}

MODE = "dark"
globals().update(PALETTES[MODE])

FONT = "Segoe UI"
FONT_BOLD = "Segoe UI Semibold"
ICON_FONT = "Segoe MDL2 Assets"

# Glyphs from the Windows icon font.
ICONS = {
    "overview": "",
    "focus": "",
    "changes": "",
    "health": "",
    "refresh": "",
    "export": "",
    "pin": "",
    "open": "",
    "sun": "",
    "moon": "",
    "signout": "",
    "warning": "",
    "calendar": "",
    "idle": "",
    "tasks": "",
    "back": "",
    "view": "",
}


def set_mode(mode):
    global MODE
    MODE = mode if mode in PALETTES else "dark"
    globals().update(PALETTES[MODE])
    return MODE


def enable_dpi_awareness():
    """Ask Windows for sharp text instead of a blurry bitmap-scaled window."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def asset_path(name):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "assets" / name


def load_fonts(root):
    """Use the bundled Plus Jakarta Sans; keep Segoe UI if it cannot be loaded."""
    global FONT, FONT_BOLD
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        for name in ("PlusJakartaSans-Regular.ttf", "PlusJakartaSans-SemiBold.ttf"):
            path = asset_path("fonts") / name
            if path.exists():
                ctypes.windll.gdi32.AddFontResourceExW(str(path), 0x10, 0)  # private to this process
        families = set(tkinter.font.families(root))
    except Exception:
        return False
    if {"Plus Jakarta Sans", "Plus Jakarta Sans SemiBold"} <= families:
        FONT, FONT_BOLD = "Plus Jakarta Sans", "Plus Jakarta Sans SemiBold"
        return True
    return False


def set_window_icon(root):
    try:
        icon = asset_path("icon.ico")
        if icon.exists():
            root.iconbitmap(default=str(icon))
    except tk.TclError:
        pass


def set_title_bar(root):
    """Match the Windows title bar to the current mode (Windows 10 20H1+ / 11)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        value = ctypes.c_int(1 if MODE == "dark" else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


def apply_styles(root):
    root.configure(bg=BG)
    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure(".", font=(FONT, 10), foreground=TEXT, background=SURFACE)
    style.configure("TFrame", background=SURFACE)
    style.configure("Bg.TFrame", background=BG)
    style.configure("Side.TFrame", background=SIDEBAR)

    style.configure("TLabel", background=SURFACE, foreground=TEXT)
    style.configure("Bg.TLabel", background=BG, foreground=MUTED)
    style.configure("PageTitle.TLabel", background=BG, foreground=TEXT, font=(FONT_BOLD, 19))
    style.configure("PageSub.TLabel", background=BG, foreground=MUTED, font=(FONT, 10))
    style.configure("Section.TLabel", background=BG, foreground=FAINT, font=(FONT_BOLD, 8))
    style.configure("Title.TLabel", font=(FONT_BOLD, 20), foreground=TEXT)
    style.configure("Heading.TLabel", font=(FONT_BOLD, 12), foreground=TEXT)
    style.configure("Sub.TLabel", foreground=MUTED)
    style.configure("Field.TLabel", font=(FONT_BOLD, 9), foreground=TEXT)
    style.configure("Hint.TLabel", font=(FONT, 9), foreground=FAINT)
    style.configure("Caps.TLabel", font=(FONT_BOLD, 8), foreground=FAINT)

    style.configure(
        "TEntry",
        padding=(10, 8),
        fieldbackground=SURFACE_2,
        foreground=TEXT,
        insertcolor=TEXT,
        bordercolor=BORDER,
        lightcolor=SURFACE_2,
        darkcolor=SURFACE_2,
        selectbackground=ACCENT,
        selectforeground=ON_ACCENT,
    )
    style.map(
        "TEntry",
        bordercolor=[("focus", ACCENT)],
        lightcolor=[("focus", SURFACE_2)],
        darkcolor=[("focus", SURFACE_2)],
    )

    style.configure(
        "Link.TButton",
        background=SURFACE,
        foreground=ACCENT,
        padding=(6, 2),
        borderwidth=0,
        font=(FONT, 9),
        focusthickness=0,
    )
    style.map("Link.TButton", background=[("active", SURFACE)], foreground=[("active", ACCENT_HOVER)])

    style.configure(
        "TCheckbutton",
        background=SURFACE,
        foreground=MUTED,
        font=(FONT, 9),
        indicatorbackground=SURFACE_2,
        indicatorforeground=ON_ACCENT,
        upperbordercolor=BORDER,
        lowerbordercolor=BORDER,
        focuscolor=SURFACE,
    )
    style.map(
        "TCheckbutton",
        background=[("active", SURFACE)],
        foreground=[("active", TEXT)],
        indicatorbackground=[("selected", ACCENT), ("active", SURFACE_2)],
    )

    style.configure(
        "Treeview",
        background=SURFACE,
        fieldbackground=SURFACE,
        foreground=TEXT,
        rowheight=34,
        borderwidth=0,
        font=(FONT, 10),
    )
    style.map("Treeview", background=[("selected", ACCENT_SOFT)], foreground=[("selected", TEXT)])
    style.configure(
        "Treeview.Heading",
        background=SURFACE,
        foreground=FAINT,
        font=(FONT_BOLD, 8),
        padding=(8, 8),
        borderwidth=0,
        relief="flat",
    )
    style.map("Treeview.Heading", background=[("active", SURFACE_2)])
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    style.configure(
        "TCombobox",
        padding=(8, 5),
        fieldbackground=SURFACE_2,
        background=SURFACE_2,
        foreground=TEXT,
        bordercolor=BORDER,
        lightcolor=SURFACE_2,
        darkcolor=SURFACE_2,
        arrowcolor=MUTED,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", SURFACE_2)],
        foreground=[("readonly", TEXT)],
        selectbackground=[("readonly", SURFACE_2)],
        selectforeground=[("readonly", TEXT)],
        bordercolor=[("focus", ACCENT)],
    )
    root.option_add("*TCombobox*Listbox.font", (FONT, 10))
    root.option_add("*TCombobox*Listbox.background", SURFACE_2)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_SOFT)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

    style.layout(
        "Vertical.TScrollbar",
        [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})],
    )
    style.configure(
        "Vertical.TScrollbar",
        background=NEUTRAL,
        troughcolor=SURFACE,
        bordercolor=SURFACE,
        lightcolor=NEUTRAL,
        darkcolor=NEUTRAL,
        gripcount=0,
        width=8,
    )
    style.map("Vertical.TScrollbar", background=[("active", FAINT)])
    return style


# Custom widgets ---------------------------------------------------------


def round_rect(canvas, x1, y1, x2, y2, radius, **kwargs):
    r = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]  # fmt: skip
    return canvas.create_polygon(points, smooth=True, **kwargs)


class Card(tk.Canvas):
    """Rounded panel. Put content in `.body`."""

    def __init__(self, parent, outer=None, fill=None, radius=14, padding=(18, 16), border=True, fixed_width=None, **kwargs):
        self.fixed_width = fixed_width
        self.fill = fill or SURFACE
        self.radius = radius
        self.pad_x, self.pad_y = padding
        self.border = BORDER if border else self.fill
        super().__init__(parent, bg=outer or BG, highlightthickness=0, bd=0, **kwargs)
        self.body = tk.Frame(self, bg=self.fill)
        self._window = self.create_window(self.pad_x, self.pad_y, window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._fit)
        self.bind("<Configure>", self._redraw)

    def _fit(self, _event=None):
        self.configure(
            width=self.fixed_width or self.body.winfo_reqwidth() + 2 * self.pad_x,
            height=self.body.winfo_reqheight() + 2 * self.pad_y,
        )

    def _redraw(self, event):
        self.delete("shape")
        round_rect(
            self, 1, 1, event.width - 1, event.height - 1, self.radius,
            fill=self.fill, outline=self.border, width=1, tags="shape",
        )  # fmt: skip
        self.tag_lower("shape")
        self.itemconfigure(
            self._window,
            width=max(event.width - 2 * self.pad_x, 1),
            height=max(event.height - 2 * self.pad_y, 1),
        )

    def make_clickable(self, command):
        """Whole card acts as a button; the border lights up on hover."""

        def bind(widget):
            widget.configure(cursor="hand2")
            widget.bind("<Button-1>", lambda _e: command(), add="+")
            for child in widget.winfo_children():
                bind(child)

        bind(self)
        self.bind("<Enter>", lambda _e: self.itemconfigure("shape", outline=ACCENT), add="+")
        self.bind("<Leave>", lambda _e: self.itemconfigure("shape", outline=self.border), add="+")


class PillButton(tk.Canvas):
    """Rounded button. kind: primary | secondary | ghost."""

    def __init__(self, parent, text, command=None, kind="primary", outer=None, icon="", width=None, height=38):
        self.kind = kind
        self.command = command
        self._text = text
        self._icon = icon
        self._state = "normal"
        self._hover = False
        self._font = (FONT_BOLD, 10)
        measure = tk.font.Font(font=self._font).measure(text) if text else 0
        natural = measure + 36 + (24 if icon else 0)
        super().__init__(
            parent, bg=outer or SURFACE, highlightthickness=0, bd=0, height=height,
            width=width or natural, cursor="hand2", takefocus=1,
        )  # fmt: skip
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", self._click)
        self.bind("<Return>", self._click)
        self.bind("<space>", self._click)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))

    def _colors(self):
        if self._state == "disabled":
            return SURFACE_2, FAINT, BORDER
        if self.kind == "primary":
            return (ACCENT_HOVER if self._hover else ACCENT), ON_ACCENT, ""
        if self.kind == "secondary":
            return (BORDER if self._hover else SURFACE_2), TEXT, BORDER
        return (SURFACE_2 if self._hover else self.cget("bg")), MUTED, ""

    def _draw(self):
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if width < 4:
            return
        fill, fg, outline = self._colors()
        round_rect(self, 1, 1, width - 1, height - 1, 10, fill=fill, outline=outline or fill, width=1)
        if self._icon and self._text:
            text_w = tk.font.Font(font=self._font).measure(self._text)
            start = (width - text_w - 22) / 2
            self.create_text(start + 7, height / 2 + 1, text=self._icon, fill=fg, font=(ICON_FONT, 10))
            self.create_text(start + 22, height / 2, text=self._text, fill=fg, font=self._font, anchor="w")
        elif self._icon:
            self.create_text(width / 2, height / 2 + 1, text=self._icon, fill=fg, font=(ICON_FONT, 11))
        else:
            self.create_text(width / 2, height / 2, text=self._text, fill=fg, font=self._font)

    def _set_hover(self, value):
        self._hover = value
        self._draw()

    def _click(self, _event=None):
        if self._state != "disabled" and self.command:
            self.command()

    def configure(self, cnf=None, **kwargs):
        if "text" in kwargs:
            self._text = kwargs.pop("text")
        if "state" in kwargs:
            self._state = str(kwargs.pop("state"))
            super().configure(cursor="arrow" if self._state == "disabled" else "hand2")
        result = super().configure(cnf, **kwargs) if (cnf or kwargs) else None
        self._draw()
        return result

    config = configure

    def cget(self, key):
        if key == "state":
            return self._state
        if key == "text":
            return self._text
        return super().cget(key)


def icon_label(parent, name, bg, fg=None, size=11):
    return tk.Label(parent, text=ICONS[name], bg=bg, fg=fg or MUTED, font=(ICON_FONT, size))


def logo(parent, bg, size=40):
    """App mark: a ring on a rounded tile."""
    canvas = tk.Canvas(parent, width=size, height=size, bg=bg, highlightthickness=0)
    round_rect(canvas, 0, 0, size, size, size * 0.3, fill=DEEP, outline=DEEP)
    inset = size * 0.27
    canvas.create_oval(inset, inset, size - inset, size - inset, outline="#ffffff", width=max(2, size // 10))
    return canvas


def attach_scrollbar(widget, parent):
    """Thin vertical scrollbar that only shows when there is something to scroll."""
    bar = ttk.Scrollbar(parent, orient="vertical", command=widget.yview)

    def update(first, last):
        bar.set(first, last)
        if float(first) <= 0.0 and float(last) >= 1.0:
            bar.pack_forget()
        elif not bar.winfo_ismapped():
            bar.pack(side="right", fill="y", padx=(4, 0), before=widget)

    widget.configure(yscrollcommand=update)
    return bar
