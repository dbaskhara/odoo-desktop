"""Windows integration without third-party packages: saved password, tray icon, notifications.

Everything degrades to "not available" on other systems or on any failure.
"""

import ctypes
import queue
import sys
import threading
from ctypes import wintypes

IS_WINDOWS = sys.platform == "win32"

# Saved password (Windows Credential Manager) --------------------------------

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def credential_target(url, database, email):
    return f"OdooDesktop:{url.strip().lower()}|{database.strip()}|{email.strip().lower()}"


def save_password(target, username, password):
    if not IS_WINDOWS:
        return False
    try:
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        blob = password.encode("utf-16-le")
        buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob) if blob else None
        cred = _CREDENTIAL()
        cred.Type = CRED_TYPE_GENERIC
        cred.TargetName = target
        cred.Comment = "Saved by Odoo Desktop"
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)) if blob else None
        cred.Persist = CRED_PERSIST_LOCAL_MACHINE
        cred.UserName = username
        advapi.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
        advapi.CredWriteW.restype = wintypes.BOOL
        return bool(advapi.CredWriteW(ctypes.byref(cred), 0))
    except Exception:
        return False


def load_password(target):
    if not IS_WINDOWS:
        return ""
    try:
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        pointer = ctypes.POINTER(_CREDENTIAL)()
        advapi.CredReadW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_CREDENTIAL)),
        ]  # fmt: skip
        advapi.CredReadW.restype = wintypes.BOOL
        advapi.CredFree.argtypes = [ctypes.c_void_p]
        if not advapi.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            return ""
        try:
            cred = pointer.contents
            raw = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
            return raw.decode("utf-16-le")
        finally:
            advapi.CredFree(pointer)
    except Exception:
        return ""


def delete_password(target):
    if not IS_WINDOWS:
        return False
    try:
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        advapi.CredDeleteW.restype = wintypes.BOOL
        return bool(advapi.CredDeleteW(target, CRED_TYPE_GENERIC, 0))
    except Exception:
        return False


# Tray icon and balloon notifications ------------------------------------------

WM_DESTROY = 0x0002
WM_HOTKEY = 0x0312
HOTKEY_ID = 1
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "OdooDesktop"
WM_COMMAND = 0x0111
WM_CLOSE = 0x0010
WM_USER = 0x0400
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
NIN_BALLOONUSERCLICK = WM_USER + 5
TRAY_MESSAGE = WM_USER + 20
WAKE_MESSAGE = WM_USER + 21

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO = 0x1
MENU_OPEN, MENU_REFRESH, MENU_QUIT = 1, 2, 3

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM) if IS_WINDOWS else None


class _GUID(ctypes.Structure):
    _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]


class _NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HANDLE),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", _GUID),
        ("hBalloonIcon", wintypes.HANDLE),
    ]


class _WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HANDLE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class TrayIcon:
    """Icon next to the clock. Events ('open', 'refresh', 'quit') arrive on `.events`."""

    def __init__(self, icon_path, tooltip="Odoo Desktop", hotkey=False):
        self._want_hotkey = hotkey  # Ctrl+Alt+N anywhere in Windows -> event "capture"
        self.hotkey = False
        self.events = queue.Queue()
        self.available = False
        self._icon_path = str(icon_path)
        self._tooltip = tooltip
        self._hwnd = None
        self._pending = queue.Queue()
        self._ready = threading.Event()
        if not IS_WINDOWS:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(3)

    # Called from the Tk thread ------------------------------------------------

    def set_tooltip(self, text):
        self._post(("tip", text[:120]))

    def notify(self, title, text):
        self._post(("balloon", (title[:60], text[:250])))

    def close(self):
        self._post(("close", None))

    def _post(self, item):
        if not self.available:
            return
        self._pending.put(item)
        try:
            self._user32.PostMessageW(self._hwnd, WAKE_MESSAGE, 0, 0)
        except Exception:
            pass

    # Runs in its own thread with a Windows message loop -----------------------

    def _run(self):
        try:
            self._setup()
        except Exception:
            self.available = False
            self._ready.set()
            return
        self._ready.set()
        message = wintypes.MSG()
        user32 = self._user32
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

    def _setup(self):
        user32 = self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        shell32 = self._shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HANDLE, wintypes.HANDLE, ctypes.c_void_p,
        ]  # fmt: skip
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASS)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.LoadImageW.argtypes = [
            wintypes.HANDLE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT
        ]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.CreatePopupMenu.restype = wintypes.HANDLE
        user32.AppendMenuW.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
        user32.TrackPopupMenu.argtypes = [
            wintypes.HANDLE, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, ctypes.c_void_p
        ]
        user32.DestroyMenu.argtypes = [wintypes.HANDLE]
        shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(_NOTIFYICONDATA)]
        shell32.Shell_NotifyIconW.restype = wintypes.BOOL
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HANDLE

        instance = kernel32.GetModuleHandleW(None)
        self._proc = WNDPROC(self._window_proc)  # keep a reference: Windows calls it
        class_name = f"OdooDesktopTray{id(self)}"
        window_class = _WNDCLASS()
        window_class.lpfnWndProc = ctypes.cast(self._proc, ctypes.c_void_p)
        window_class.hInstance = instance
        window_class.lpszClassName = class_name
        if not user32.RegisterClassW(ctypes.byref(window_class)):
            raise OSError("RegisterClassW failed")
        self._hwnd = user32.CreateWindowExW(0, class_name, "Odoo Desktop", 0, 0, 0, 0, 0, None, None, instance, None)
        if not self._hwnd:
            raise OSError("CreateWindowExW failed")

        icon = user32.LoadImageW(None, self._icon_path, 1, 16, 16, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
        data = self._data = _NOTIFYICONDATA()
        data.cbSize = ctypes.sizeof(_NOTIFYICONDATA)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = NIF_MESSAGE | NIF_TIP | (NIF_ICON if icon else 0)
        data.uCallbackMessage = TRAY_MESSAGE
        data.hIcon = icon
        data.szTip = self._tooltip
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)):
            raise OSError("Shell_NotifyIconW failed")
        self.available = True
        if self._want_hotkey:
            # Fails harmlessly when another program already owns the shortcut.
            self.hotkey = bool(
                user32.RegisterHotKey(self._hwnd, HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("N"))
            )

    def _window_proc(self, hwnd, message, wparam, lparam):
        user32 = self._user32
        if message == TRAY_MESSAGE:
            event = lparam & 0xFFFF
            if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK, NIN_BALLOONUSERCLICK):
                self.events.put("open")
            elif event == WM_RBUTTONUP:
                self._show_menu()
            return 0
        if message == WM_COMMAND:
            command = wparam & 0xFFFF
            name = {MENU_OPEN: "open", MENU_REFRESH: "refresh", MENU_QUIT: "quit"}.get(command)
            if name:
                self.events.put(name)
            return 0
        if message == WAKE_MESSAGE:
            self._drain()
            return 0
        if message == WM_HOTKEY:
            self.events.put("capture")
            return 0
        if message == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _show_menu(self):
        user32 = self._user32
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, 0, MENU_OPEN, "Open Odoo Desktop")
        user32.AppendMenuW(menu, 0, MENU_REFRESH, "Refresh now")
        user32.AppendMenuW(menu, 0x800, 0, None)  # separator
        user32.AppendMenuW(menu, 0, MENU_QUIT, "Quit")
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        user32.SetForegroundWindow(self._hwnd)
        user32.TrackPopupMenu(menu, 0, point.x, point.y, 0, self._hwnd, None)
        user32.PostMessageW(self._hwnd, 0, 0, 0)
        user32.DestroyMenu(menu)

    def _drain(self):
        while True:
            try:
                kind, payload = self._pending.get_nowait()
            except queue.Empty:
                return
            data = self._data
            if kind == "tip":
                data.uFlags = NIF_TIP
                data.szTip = payload
                self._shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))
            elif kind == "balloon":
                data.uFlags = NIF_INFO
                data.szInfoTitle = payload[0]
                data.szInfo = payload[1]
                data.dwInfoFlags = NIIF_INFO
                self._shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))
            elif kind == "close":
                self.available = False
                self._shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
                self._user32.DestroyWindow(self._hwnd)
                return


# Start with Windows -----------------------------------------------------------


def autostart_command():
    """The command Windows runs at sign-in: the app, hidden next to the clock."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    from pathlib import Path

    pythonw = Path(sys.executable).with_name("pythonw.exe")
    main = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{pythonw}" "{main}" --tray'


def autostart_enabled():
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, RUN_NAME)
        return bool(value)
    except (ImportError, OSError):
        return False


def set_autostart(enabled):
    """Add or remove the app in the current user's start-up programs. Returns True when done."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except (ImportError, OSError):
        return False
