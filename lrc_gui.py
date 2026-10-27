#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pebby Lyrics 歌词工具（图形界面）。

依赖与命令行工具相同：Mutagen、OpenCC。源码运行：
    python lrc_gui.py
"""

from __future__ import annotations

import ctypes
from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import traceback
import webbrowser

import update_check
from version import __version__

try:
    import prepare_lrc as core
except ImportError:  # 允许只拷贝 lrc_gui.py 后从任意目录运行
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import prepare_lrc as core


APP_TITLE = "Pebby Lyrics 歌词工具"
PREVIEW_DIR_NAME = "_pebby_preview"
FORMAT_OPTIONS = [
    (".mp3", "MP3"),
    (".wav", "WAV"),
    (".flac", "FLAC"),
    (".m4a", "M4A"),
    (".ogg", "OGG"),
    (".opus", "OPUS"),
]
BG = "#1e1e1e"
PANEL = "#252526"
FIELD = "#333337"
FG = "#e6e6e6"
MUTED = "#9d9d9d"
GREEN = "#4ec9b0"
RED = "#f48771"
YELLOW = "#dcdcaa"
CYAN = "#569cd6"
WHITE = "#ffffff"


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "Pebby-Lyrics"
    return Path.home() / ".pebby-lyrics"


def send_to_recycle_bin(paths: list[str]) -> bool:
    """把一批文件移到 Windows 回收站；非 Windows 或失败返回 False。"""
    if os.name != "nt" or not paths:
        return False
    import ctypes.wintypes as wt

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wt.HWND),
            ("wFunc", wt.UINT),
            ("pFrom", wt.LPCWSTR),
            ("pTo", wt.LPCWSTR),
            ("fFlags", wt.WORD),
            ("fAnyOperationsAborted", wt.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wt.LPCWSTR),
        ]

    source = "\0".join(str(Path(item).resolve()) for item in paths) + "\0\0"
    operation = SHFILEOPSTRUCTW()
    operation.hwnd = None
    operation.wFunc = 3  # FO_DELETE
    operation.pFrom = source
    operation.pTo = None
    operation.fFlags = 0x0040 | 0x0010 | 0x0004 | 0x0400  # ALLOWUNDO|NOCONFIRM|SILENT|NOERRORUI
    operation.fAnyOperationsAborted = 0
    operation.hNameMappings = None
    operation.lpszProgressTitle = None
    try:
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
        return result == 0 and not operation.fAnyOperationsAborted
    except Exception:
        return False


def find_lrc_files(root: Path, recursive: bool) -> list[Path]:
    if not root.is_dir():
        return []
    if not recursive:
        return sorted(item for item in root.iterdir()
                      if item.is_file() and not item.is_symlink() and item.suffix.casefold() == ".lrc")
    found = []
    for directory, dirs, names in os.walk(root):
        dirs[:] = sorted(name for name in dirs if not (Path(directory) / name).is_symlink())
        for name in sorted(names):
            path = Path(directory) / name
            if path.is_file() and not path.is_symlink() and path.suffix.casefold() == ".lrc":
                found.append(path)
    return found


class CheckItem(tk.Frame):
    """深色界面的勾选项：选中显示绿色 √，不再使用容易误解的 X。"""

    def __init__(self, master, text: str, variable: tk.BooleanVar, **kwargs):
        super().__init__(master, bg=BG, **kwargs)
        self.variable = variable
        self.indicator = tk.Label(
            self, text="", width=2, height=1, relief="solid", borderwidth=1,
            bg=FIELD, fg=MUTED, font=("Segoe UI Symbol", 11, "bold"),
            anchor="center", padx=0, pady=0)
        self.indicator.pack(side="left")
        self.label = tk.Label(
            self, text=text, bg=BG, fg=FG, font=("Microsoft YaHei UI", 10),
            anchor="w")
        self.label.pack(side="left", padx=(5, 0))
        for widget in (self, self.indicator, self.label):
            widget.bind("<Button-1>", self._toggle)
            widget.configure(cursor="hand2")
        self.variable.trace_add("write", self._refresh)
        self._refresh()

    def _toggle(self, _event=None):
        self.variable.set(not bool(self.variable.get()))

    def _refresh(self, *_args):
        checked = bool(self.variable.get())
        self.indicator.configure(text="✓" if checked else "",
                                 fg=GREEN if checked else MUTED)


def open_in_explorer(path: Path) -> bool:
    """打开资源管理器窗口。

    只用两条不创建子进程的路：
    1. ``os.startfile``：交给 shell 关联处理；
    2. ``Shell.Application`` COM 的 ``Explore``：让已在运行的文件管理器打开目录。

    不启动 explorer.exe / powershell.exe——在受限或提权环境里创建子进程会被拒绝
    （0xC0000142、Access denied），用户只会看到系统错误框。
    """
    try:
        os.startfile(str(path))
        return True
    except OSError:
        pass
    return explore_via_com(path)


def _com_guid(text):
    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    guid = GUID()
    hr = ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(guid))
    if hr != 0:
        raise OSError(f"CLSIDFromString 失败: 0x{hr & 0xFFFFFFFF:08x}")
    return guid


def _explore_com(path: str) -> bool:
    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    class VARIANT(ctypes.Structure):
        _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort),
                    ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                    ("value", ctypes.c_void_p), ("pad", ctypes.c_void_p)]

    class DISPPARAMS(ctypes.Structure):
        _fields_ = [("rgvarg", ctypes.POINTER(VARIANT)),
                    ("rgdispidNamedArgs", ctypes.POINTER(ctypes.c_long)),
                    ("cArgs", ctypes.c_uint), ("cNamedArgs", ctypes.c_uint)]

    ole32 = ctypes.OleDLL("ole32")
    oleaut = ctypes.OleDLL("oleaut32")

    # 已有 COM 初始化时返回 RPC_E_CHANGED_MODE，可以忽略
    ole32.CoInitializeEx(None, 2)

    clsid = _com_guid("{13709620-C279-11CE-A49E-444553540000}")   # Shell.Application
    iid = _com_guid("{00020400-0000-0000-C000-000000000046}")     # IDispatch
    pointer = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, 4, ctypes.byref(iid),
                                ctypes.byref(pointer))
    if hr != 0 or not pointer.value:
        return False

    vtable = ctypes.cast(ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0],
                         ctypes.POINTER(ctypes.c_void_p))
    get_ids = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(GUID),
                                 ctypes.POINTER(ctypes.c_wchar_p), ctypes.c_uint,
                                 ctypes.c_ulong, ctypes.POINTER(ctypes.c_long))(vtable[5])
    invoke = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_long,
                                ctypes.POINTER(GUID), ctypes.c_ulong, ctypes.c_ushort,
                                ctypes.POINTER(DISPPARAMS), ctypes.c_void_p,
                                ctypes.c_void_p, ctypes.c_void_p)(vtable[6])
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])

    try:
        nil = GUID()
        name = ctypes.c_wchar_p("Explore")
        dispid = ctypes.c_long()
        hr = get_ids(pointer, ctypes.byref(nil), ctypes.byref(name), 1, 0,
                     ctypes.byref(dispid))
        if hr != 0:
            return False
        bstr = oleaut.SysAllocString(ctypes.c_wchar_p(path))
        if not bstr:
            return False
        try:
            argument = VARIANT(vt=8, r1=0, r2=0, r3=0, value=bstr, pad=None)  # VT_BSTR
            params = DISPPARAMS(rgvarg=ctypes.pointer(argument), rgdispidNamedArgs=None,
                                cArgs=1, cNamedArgs=0)
            # DISPATCH_METHOD = 1
            hr = invoke(pointer, dispid, ctypes.byref(nil), 0, 1, ctypes.byref(params),
                        None, None, None)
        finally:
            oleaut.SysFreeString(bstr)
        return hr == 0
    finally:
        release(pointer)


def explore_via_com(path: Path) -> bool:
    """用 Shell.Application COM 打开目录；任何异常都当作“没打开”。"""
    if os.name != "nt":
        return False
    try:
        return _explore_com(str(path))
    except (OSError, AttributeError):
        return False


def open_with_notepad(path: Path) -> bool:
    """用记事本打开文件（预览结果是临时文件，双击关联不一定可用）。"""
    try:
        subprocess.Popen(["notepad.exe", str(path)],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except OSError:
        return False


class TextViewer(tk.Toplevel):
    """应用内文本查看器。

    杀软/沙箱环境里启动记事本可能被拦，用户点了链接却什么都没看到；
    这里先把内容直接显示出来，能不能调外部程序只是附加能力。
    """

    def __init__(self, master, title: str, content: str, path: Path | None = None):
        super().__init__(master)
        self.title(title)
        self.configure(bg=BG)
        self.geometry("780x560")
        self.minsize(520, 360)
        self.path = path

        wrap = ttk.Frame(self, padding=10)
        wrap.pack(fill="both", expand=True)
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(1, weight=1)

        ttk.Label(wrap, text=str(path) if path else "",
                  foreground=MUTED).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

        text = tk.Text(wrap, wrap="none", bg="#151515", fg=WHITE, insertbackground=WHITE,
                       relief="flat", font=("Consolas", 10), padx=8, pady=6)
        text.grid(row=1, column=0, sticky="nsew")
        text.insert("1.0", content)
        text.configure(state="disabled")
        yscroll = ttk.Scrollbar(wrap, orient="vertical", command=text.yview)
        yscroll.grid(row=1, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(wrap, orient="horizontal", command=text.xview)
        xscroll.grid(row=2, column=0, sticky="ew")
        text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        bar = ttk.Frame(wrap)
        bar.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(bar, text="关闭", command=self.destroy).pack(side="right")
        if path is not None:
            ttk.Button(bar, text="用记事本打开",
                       command=self._open_external).pack(side="right", padx=(0, 6))
        ttk.Button(bar, text="复制全文",
                   command=lambda: self._copy(content)).pack(side="right", padx=(0, 6))

    def _copy(self, content: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(content)

    def _open_external(self) -> None:
        if self.path is not None and not open_with_notepad(self.path):
            messagebox.showerror(APP_TITLE, f"系统不允许启动记事本，可以直接看这个窗口里的内容。\n{self.path}")


class FolderBrowser(tk.Toplevel):
    """应用内目录浏览：外部资源管理器打不开时的兜底。"""

    def __init__(self, master, folder: Path):
        super().__init__(master)
        self.title(f"目录内容 - {folder.name or folder}")
        self.configure(bg=BG)
        self.geometry("760x500")
        self.minsize(520, 360)
        self.folder = folder

        wrap = ttk.Frame(self, padding=10)
        wrap.pack(fill="both", expand=True)
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(2, weight=1)

        bar = ttk.Frame(wrap)
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)
        ttk.Button(bar, text="上一级", command=self._go_up).grid(row=0, column=0, padx=(0, 6))
        self.path_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.path_var, foreground=FG).grid(row=0, column=1, sticky="w")

        ttk.Label(wrap, text="系统不允许自动打开资源管理器，这里是应用内浏览（路径已复制到剪贴板）",
                  foreground=MUTED).grid(row=1, column=0, sticky="w", pady=(6, 4))

        body = ttk.Frame(wrap)
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(body, columns=("size",), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="名称", anchor="w")
        self.tree.heading("size", text="大小", anchor="e")
        self.tree.column("#0", width=480, minwidth=260, anchor="w")
        self.tree.column("size", width=100, minwidth=90, anchor="e", stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.tag_configure("dir", foreground=GREEN)
        self.tree.tag_configure("file", foreground=MUTED)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Double-1>", self._enter)

        footer = ttk.Frame(wrap)
        footer.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(footer, text="关闭", command=self.destroy).pack(side="right")
        ttk.Button(footer, text="复制路径",
                   command=self._copy_path).pack(side="right", padx=(0, 6))
        self._load(folder)

    def _copy_path(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(str(self.folder))

    def _go_up(self) -> None:
        if self.folder.parent != self.folder:
            self._load(self.folder.parent)

    def _enter(self, _event=None) -> None:
        item = self.tree.focus()
        if item and item != "..":
            target = Path(item)
            if target.is_dir():
                self._load(target)

    def _load(self, folder: Path) -> None:
        try:
            entries = list(folder.iterdir())
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"无法读取目录：{exc}", parent=self)
            return
        self.folder = folder
        self.path_var.set(str(folder))
        self.title(f"目录内容 - {folder.name or folder}")
        self.tree.delete(*self.tree.get_children())
        dirs = sorted((item for item in entries if item.is_dir()), key=lambda item: item.name.casefold())
        files = sorted((item for item in entries if item.is_file()), key=lambda item: item.name.casefold())
        if folder.parent != folder:
            self.tree.insert("", "end", iid="..", text="..（上一级）", values=("",), tags=("dir",))
        for item in dirs:
            self.tree.insert("", "end", iid=str(item), text=item.name, values=("",), tags=("dir",))
        for item in files:
            try:
                size = f"{item.stat().st_size / 1024:.0f} KB"
            except OSError:
                size = ""
            self.tree.insert("", "end", iid=str(item), text=item.name, values=(size,), tags=("file",))


class LrcApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.queue: queue.Queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.running = False
        self.mode = "generate"
        self.results: list[dict] = []
        self.summary = Counter()
        self.total = 0
        self.done = 0
        self.scan_errors: list[str] = []
        self._closing = False
        self.checking_update = False
        self.dismissed_version = ""
        self.last_check_date = ""
        self.settings_path = app_data_dir() / "settings.json"

        self.folder_var = tk.StringVar()
        self.recursive_var = tk.BooleanVar(value=False)
        self.prefer_var = tk.StringVar(value="simplified")
        self.overwrite_var = tk.BooleanVar(value=False)
        self.credits_var = tk.BooleanVar(value=False)
        self.convert_var = tk.BooleanVar(value=False)
        self.preview_var = tk.BooleanVar(value=False)
        self.duration_var = tk.DoubleVar(value=core.DURATION_TOLERANCE)
        self.end_tolerance_var = tk.DoubleVar(value=core.END_TIME_TOLERANCE)
        self.max_line_var = tk.IntVar(value=core.DEFAULT_MAX_LINE_CHARS)
        self.source_var = tk.StringVar(value="auto")
        self.encoding_var = tk.StringVar(value="utf-8")
        self.name_format_var = tk.StringVar(value=core.NAME_FORMATS[core.DEFAULT_NAME_FORMAT])
        self.format_vars = {suffix: tk.BooleanVar(value=True) for suffix, _ in FORMAT_OPTIONS}
        self.check_updates_var = tk.BooleanVar(value=False)
        self._link_seq = 0
        self.preview_dir: Path | None = None
        self._preview_dirs: list[Path] = []

        self._build_style()
        self._build_ui()
        self._load_settings()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._poll_queue)
        self.root.after(2500, self._auto_check_updates)

    # ------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------
    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=FG, fieldbackground=FIELD)
        for name in ("TFrame", "TLabelframe", "TLabelframe.Label", "TLabel",
                     "TCheckbutton", "TRadiobutton"):
            style.configure(name, background=BG, foreground=FG)
        style.configure("TButton", background=FIELD, foreground=FG, padding=(10, 4))
        style.map("TButton", background=[("active", "#45454a")],
                  foreground=[("disabled", MUTED)])
        style.configure("TEntry", fieldbackground=FIELD, foreground=FG, insertcolor=FG)
        # clam 默认把 Spinbox 箭头画成黑色，在深色底上完全看不见
        style.configure("TSpinbox", fieldbackground=FIELD, foreground=FG, insertcolor=FG,
                        arrowcolor=FG, arrowsize=14, background="#45454a",
                        bordercolor=FIELD, lightcolor="#45454a", darkcolor="#45454a")
        style.map("TSpinbox", fieldbackground=[("disabled", FIELD)],
                  foreground=[("disabled", MUTED)],
                  arrowcolor=[("disabled", MUTED)])
        style.configure("TCombobox", fieldbackground=FIELD, background=FIELD,
                        foreground=FG, arrowcolor=FG)
        style.map("TCombobox", fieldbackground=[("readonly", FIELD)])
        style.configure("Treeview", background=FIELD, fieldbackground=FIELD,
                        foreground=FG, bordercolor=FIELD, rowheight=22)
        style.map("Treeview", background=[("selected", "#094771")],
                  foreground=[("selected", WHITE)])
        style.configure("Treeview.Heading", background="#2d2d30", foreground=FG,
                        relief="flat", padding=(6, 4))
        style.configure("Horizontal.TProgressbar", background=GREEN, troughcolor=FIELD,
                        bordercolor=FIELD, lightcolor=GREEN, darkcolor=GREEN)

    def _build_ui(self) -> None:
        self.root.title(f"{APP_TITLE} v{__version__}")
        self.root.geometry("940x700")
        self.root.minsize(780, 560)
        self.root.configure(bg=BG)

        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(4, weight=1)

        folder_row = ttk.Frame(main)
        folder_row.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        folder_row.columnconfigure(1, weight=1)
        ttk.Label(folder_row, text="音乐目录").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.folder_entry = ttk.Entry(folder_row, textvariable=self.folder_var)
        self.folder_entry.grid(row=0, column=1, sticky="ew")
        self.browse_button = ttk.Button(folder_row, text="选择文件夹", command=self._choose_folder)
        self.browse_button.grid(row=0, column=2, padx=(8, 0))

        options = ttk.LabelFrame(main, text="选项", padding=10)
        options.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        options.columnconfigure(1, weight=1)
        options.columnconfigure(3, weight=1)

        CheckItem(options, text="递归子文件夹", variable=self.recursive_var).grid(
            row=0, column=0, sticky="w", padx=(0, 18))
        prefer = ttk.Frame(options)
        prefer.grid(row=0, column=1, sticky="w")
        ttk.Label(prefer, text="优先文字").pack(side="left", padx=(0, 6))
        ttk.Radiobutton(prefer, text="简体", value="simplified", variable=self.prefer_var).pack(side="left")
        ttk.Radiobutton(prefer, text="繁体", value="traditional", variable=self.prefer_var).pack(
            side="left", padx=(8, 0))

        ttk.Label(options, text="识别格式").grid(row=0, column=2, sticky="e", padx=(18, 6))
        formats = ttk.Frame(options)
        formats.grid(row=0, column=3, sticky="w")
        for index, (suffix, label) in enumerate(FORMAT_OPTIONS):
            CheckItem(formats, text=label, variable=self.format_vars[suffix]).grid(
                row=0, column=index, padx=(0, 4))

        CheckItem(options, text="覆盖已有 LRC", variable=self.overwrite_var).grid(
            row=1, column=0, sticky="w", pady=(8, 0))
        CheckItem(options, text="移除片头信息", variable=self.credits_var).grid(
            row=1, column=1, sticky="w", pady=(8, 0))
        CheckItem(options, text="仅预览不写文件", variable=self.preview_var).grid(
            row=1, column=2, columnspan=2, sticky="w", pady=(8, 0))

        limits = ttk.Frame(options)
        limits.grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(limits, text="最大时长差(秒)").pack(side="left")
        ttk.Spinbox(limits, from_=0.0, to=60.0, increment=0.5, width=6,
                    textvariable=self.duration_var).pack(side="left", padx=(4, 16))
        ttk.Label(limits, text="结尾容差(秒)").pack(side="left")
        ttk.Spinbox(limits, from_=0.0, to=120.0, increment=1.0, width=6,
                    textvariable=self.end_tolerance_var).pack(side="left", padx=(4, 16))
        ttk.Label(limits, text="行长(字)").pack(side="left")
        ttk.Spinbox(limits, from_=0, to=80, increment=1, width=5,
                    textvariable=self.max_line_var).pack(side="left", padx=(4, 16))
        CheckItem(limits, text="强制转简体", variable=self.convert_var).pack(side="left")

        advanced = ttk.Frame(options)
        advanced.grid(row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(advanced, text="来源").pack(side="left")
        ttk.Combobox(advanced, width=8, state="readonly", textvariable=self.source_var,
                     values=("auto", "qq", "lrclib")).pack(side="left", padx=(4, 16))
        ttk.Label(advanced, text="编码").pack(side="left")
        ttk.Combobox(advanced, width=8, state="readonly", textvariable=self.encoding_var,
                     values=("utf-8", "gbk")).pack(side="left", padx=(4, 16))
        CheckItem(advanced, text="启动时检查更新", variable=self.check_updates_var).pack(
            side="left")

        naming = ttk.Frame(options)
        naming.grid(row=4, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(naming, text="文件名格式").pack(side="left")
        ttk.Combobox(naming, width=14, state="readonly", textvariable=self.name_format_var,
                     values=tuple(core.NAME_FORMATS.values())).pack(side="left", padx=(4, 8))
        ttk.Label(naming, text="只有“与音频同名”能被按文件名查找歌词的播放器识别",
                  foreground=MUTED).pack(side="left")

        actions = ttk.Frame(main)
        actions.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.start_button = ttk.Button(actions, text="开始生成", command=self._start)
        self.start_button.pack(side="left")
        self.cancel_button = ttk.Button(actions, text="取消", command=self._cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=(8, 0))
        self.delete_button = ttk.Button(actions, text="删除歌词", command=self._delete_lrc)
        self.delete_button.pack(side="left", padx=(8, 0))
        self.report_button = ttk.Button(actions, text="保存报告", command=self._save_report,
                                        state="disabled")
        self.report_button.pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="清除日志", command=self._clear_log).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="检查更新", command=self._manual_check).pack(
            side="right", padx=(0, 8))
        ttk.Button(actions, text="打开目录", command=self._open_folder).pack(side="right")

        progress_row = ttk.Frame(main)
        progress_row.grid(row=3, column=0, sticky="ew")
        progress_row.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(progress_row, mode="determinate", maximum=1)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.progress_label = ttk.Label(progress_row, text="就绪", width=18, anchor="e")
        self.progress_label.grid(row=0, column=1, padx=(8, 0))

        log_frame = ttk.LabelFrame(main, text="日志", padding=6)
        log_frame.grid(row=4, column=0, rowspan=1, sticky="nsew", pady=(8, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        main.rowconfigure(4, weight=1)
        self.log_text = tk.Text(log_frame, wrap="word", bg="#151515", fg=WHITE,
                                insertbackground=WHITE, relief="flat", borderwidth=0,
                                font=("Consolas", 10), padx=8, pady=6, state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.tag_configure("time", foreground=MUTED)
        self.log_text.tag_configure("info", foreground=WHITE)
        self.log_text.tag_configure("success", foreground=GREEN)
        self.log_text.tag_configure("error", foreground=RED)
        self.log_text.tag_configure("warn", foreground=YELLOW)
        self.log_text.tag_configure("summary", foreground=CYAN, font=("Consolas", 10, "bold"))
        self.log_text.tag_configure("muted", foreground=MUTED)

        self.summary_label = ttk.Label(main, text="尚未开始", anchor="w")
        self.summary_label.grid(row=5, column=0, sticky="ew", pady=(8, 0))

    # ------------------------------------------------------------
    # 设置
    # ------------------------------------------------------------
    def _load_settings(self) -> None:
        try:
            if not self.settings_path.exists():
                return
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if isinstance(data.get("folder"), str):
                self.folder_var.set(data["folder"])
            self.recursive_var.set(bool(data.get("recursive", False)))
            if data.get("prefer") in {"simplified", "traditional"}:
                self.prefer_var.set(data["prefer"])
            self.overwrite_var.set(bool(data.get("overwrite", False)))
            self.credits_var.set(bool(data.get("credits", False)))
            self.convert_var.set(bool(data.get("convert", False)))
            self.preview_var.set(bool(data.get("preview", False)))
            self.check_updates_var.set(bool(data.get("check_updates", False)))
            if data.get("name_format") in core.NAME_FORMATS:
                self.name_format_var.set(core.NAME_FORMATS[data["name_format"]])
            if isinstance(data.get("dismissed_version"), str):
                self.dismissed_version = data["dismissed_version"]
            if isinstance(data.get("last_check"), str):
                self.last_check_date = data["last_check"]
            if isinstance(data.get("duration"), (int, float)):
                self.duration_var.set(float(data["duration"]))
            if isinstance(data.get("end_tolerance"), (int, float)):
                self.end_tolerance_var.set(float(data["end_tolerance"]))
            if isinstance(data.get("max_line"), int):
                self.max_line_var.set(int(data["max_line"]))
            if data.get("source") in {"auto", "qq", "lrclib"}:
                self.source_var.set(data["source"])
            if data.get("encoding") in {"utf-8", "gbk"}:
                self.encoding_var.set(data["encoding"])
            formats = data.get("formats")
            if isinstance(formats, list):
                for suffix, var in self.format_vars.items():
                    var.set(suffix in formats)
        except (OSError, ValueError, TypeError):
            pass

    def _save_settings(self) -> None:
        data = {
            "folder": self.folder_var.get().strip(),
            "recursive": self.recursive_var.get(),
            "prefer": self.prefer_var.get(),
            "overwrite": self.overwrite_var.get(),
            "credits": self.credits_var.get(),
            "convert": self.convert_var.get(),
            "preview": self.preview_var.get(),
            "duration": self.duration_var.get(),
            "end_tolerance": self.end_tolerance_var.get(),
            "max_line": self.max_line_var.get(),
            "source": self.source_var.get(),
            "encoding": self.encoding_var.get(),
            "name_format": next((key for key, label in core.NAME_FORMATS.items()
                                 if label == self.name_format_var.get()), core.DEFAULT_NAME_FORMAT),
            "formats": [suffix for suffix, var in self.format_vars.items() if var.get()],
            "check_updates": self.check_updates_var.get(),
            "dismissed_version": self.dismissed_version,
            "last_check": self.last_check_date,
        }
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------
    # 更新检查（只读公开 Releases，不自替换、不上报）
    # ------------------------------------------------------------
    def _auto_check_updates(self) -> None:
        if not self.check_updates_var.get():
            return
        # 每个自然日最多自动查询一次，避免反复启动时刷 GitHub 接口
        if self.last_check_date == datetime.now().date().isoformat():
            return
        self._start_update_check(manual=False)

    def _manual_check(self) -> None:
        self._start_update_check(manual=True)

    def _start_update_check(self, manual: bool) -> None:
        if self.checking_update:
            return
        self.checking_update = True
        if manual:
            self._log("info", "正在检查更新…")
        threading.Thread(target=self._update_worker, args=(manual,), daemon=True).start()

    def _update_worker(self, manual: bool) -> None:
        # 网络请求放在后台线程，结果经队列回界面线程，避免阻塞 Tk
        self.queue.put(("update", update_check.check(), manual))

    def _on_update(self, result: dict, manual: bool) -> None:
        self.checking_update = False
        if not manual:
            # 自动检查无论成功与否都记下当天，避免失败时反复重试骚扰
            self.last_check_date = datetime.now().date().isoformat()
            self._save_settings()
        status = result.get("status")
        if status == "error":
            if manual:
                message = str(result.get("message", "未知错误"))
                self._log("warn", f"检查更新失败：{message}")
                messagebox.showwarning(APP_TITLE, f"检查更新失败：{message}")
            return
        if status == "latest":
            self._log("muted", f"已是最新版本 v{__version__}")
            if manual:
                messagebox.showinfo(APP_TITLE, f"当前 v{__version__} 已是最新版本。")
            return
        version = str(result.get("version") or "")
        if version and not manual and version == self.dismissed_version:
            return
        self._log("success", f"发现新版本 v{version}（当前 v{__version__}）")
        detail = ""
        notes = str(result.get("notes") or "").strip()
        if notes:
            detail = "\n\n" + notes.splitlines()[0][:120]
        answer = messagebox.askyesno(
            f"{APP_TITLE} · 检查更新",
            f"发现新版本 v{version}（当前 v{__version__}）。{detail}"
            "\n\n是否前往发布页下载？",
        )
        if answer:
            webbrowser.open(str(result.get("url") or update_check.RELEASES_PAGE))
        elif version:
            # 记下被跳过的版本，同一版本不再自动打扰
            self.dismissed_version = version
            self._save_settings()

    # ------------------------------------------------------------
    # 日志
    # ------------------------------------------------------------
    def _log(self, kind: str, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{stamp}] ", "time")
        self.log_text.insert("end", message + "\n", kind)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _log_link(self, text: str, target, opener) -> None:
        """写一行可点击的日志，点一下就用 opener(target) 打开。"""
        self.log_text.configure(state="normal")
        start = self.log_text.index("end-1c")
        self.log_text.insert("end", text + "\n", "info")
        end = self.log_text.index("end-1c")
        tag = f"link{self._link_seq}"
        self._link_seq += 1
        self.log_text.tag_add(tag, start, end)
        self.log_text.tag_configure(tag, foreground=CYAN, underline=True)
        self.log_text.tag_bind(tag, "<Button-1>", lambda _event, item=target: opener(item))
        self.log_text.tag_bind(tag, "<Enter>",
                               lambda _event: self.log_text.configure(cursor="hand2"))
        self.log_text.tag_bind(tag, "<Leave>",
                               lambda _event: self.log_text.configure(cursor=""))
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _open_preview_file(self, path) -> None:
        target = Path(path)
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"无法读取预览文件：{exc}")
            return
        TextViewer(self.root, f"预览 - {target.name}", content, target)

    def _open_preview_dir(self, path) -> None:
        self._reveal_folder(Path(path))

    def _reveal_folder(self, folder: Path) -> None:
        """优先交给资源管理器；shell 被系统拦住时退回应用内浏览。"""
        if open_in_explorer(folder):
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(str(folder))
        FolderBrowser(self.root, folder)

    def _cleanup_previews(self) -> None:
        """删掉本次运行生成的预览目录，顺手收掉空的父目录。"""
        for item in list(self._preview_dirs):
            shutil.rmtree(item, ignore_errors=True)
            parent = item.parent
            try:
                if parent.name == PREVIEW_DIR_NAME and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass
        self._preview_dirs = []

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    # ------------------------------------------------------------
    # 文件夹/按钮
    # ------------------------------------------------------------
    def _choose_folder(self) -> None:
        current = self.folder_var.get().strip()
        initial = current if Path(current).is_dir() else str(Path.home() / "Desktop")
        patterns = " ".join(f"*{suffix}" for suffix in sorted(core.AUDIO_SUFFIXES))
        chosen = filedialog.askopenfilename(
            parent=self.root,
            title="选择音乐目录：选中音频文件即用其所在目录，也可在文件名框直接输入路径",
            initialdir=initial,
            filetypes=[("音频文件", patterns), ("所有文件", "*.*")])
        if not chosen:
            return
        picked = Path(chosen)
        # 选到文件就用它所在的目录；选到目录（手输路径时可能）就直接用
        self.folder_var.set(str(picked if picked.is_dir() else picked.parent))

    def _open_folder(self) -> None:
        folder = Path(self.folder_var.get().strip())
        if not folder.is_dir():
            messagebox.showerror(APP_TITLE, "请先选择有效的音乐目录。")
            return
        self._reveal_folder(folder)

    def _set_running(self, running: bool, mode: str = "generate") -> None:
        self.running = running
        self.mode = mode
        self.start_button.configure(state="disabled" if running else "normal")
        self.delete_button.configure(state="disabled" if running else "normal")
        self.cancel_button.configure(state="normal" if running else "disabled")
        if not running:
            self.progress_label.configure(text="就绪")

    # ------------------------------------------------------------
    # 生成
    # ------------------------------------------------------------
    def _start(self) -> None:
        if self.running:
            return
        folder = Path(self.folder_var.get().strip())
        if not folder.is_dir():
            messagebox.showerror(APP_TITLE, "请选择有效的音乐目录。")
            return
        formats = {suffix for suffix, var in self.format_vars.items() if var.get()}
        if not formats:
            messagebox.showerror(APP_TITLE, "至少选择一种音频格式。")
            return
        try:
            duration = float(self.duration_var.get())
            end_tolerance = float(self.end_tolerance_var.get())
            max_line = int(self.max_line_var.get())
        except (ValueError, tk.TclError):
            messagebox.showerror(APP_TITLE, "时长差、结尾容差和行长必须是数字。")
            return
        if duration < 0 or end_tolerance < 0 or max_line < 0:
            messagebox.showerror(APP_TITLE, "时长差、结尾容差和行长不能为负数。")
            return
        self._save_settings()
        self._clear_log()
        self.results = []
        self.summary = Counter()
        self.total = 0
        self.done = 0
        self.scan_errors = []
        self.progress.configure(maximum=1, value=0)
        self.progress_label.configure(text="扫描中…")
        self.summary_label.configure(text="正在处理…")
        self.report_button.configure(state="disabled")
        self._set_running(True, "generate")
        self.cancel_event.clear()
        self._log("info", f"开始扫描：{folder}")
        self._cleanup_previews()
        self.preview_dir = None
        if self.preview_var.get():
            self._log("warn", "预览模式：只查询和校验，不写正式的 .lrc。")
            candidate = (folder / PREVIEW_DIR_NAME
                         / datetime.now().strftime("%Y%m%d-%H%M%S"))
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                self.preview_dir = candidate
                self._preview_dirs.append(candidate)
                self._log_link(f"预览文件会写到 {candidate}（点击打开该目录）",
                               candidate, self._open_preview_dir)
            except OSError as exc:
                reason = getattr(exc, "strerror", None) or str(exc)
                self._log("warn", f"无法在音乐目录下创建预览目录 {candidate}：{reason}")
                self.preview_dir = None
        name_format = next((key for key, label in core.NAME_FORMATS.items()
                            if label == self.name_format_var.get()), core.DEFAULT_NAME_FORMAT)
        if name_format != core.DEFAULT_NAME_FORMAT:
            self._log("warn", "当前文件名格式不是“与音频同名”，按文件名找歌词的播放器可能识别不到。")
        args = (
            folder, formats, self.recursive_var.get(), self.prefer_var.get(),
            self.overwrite_var.get(), self.credits_var.get(), self.convert_var.get(),
            self.preview_var.get(), duration, end_tolerance, max_line,
            self.source_var.get(), self.encoding_var.get(), name_format,
            self.preview_dir,
        )
        self.worker = threading.Thread(target=self._generate_worker, args=args, daemon=True)
        self.worker.start()

    def _generate_worker(self, folder: Path, formats: set[str], recursive: bool,
                         prefer: str, overwrite: bool, credits: bool, convert: bool,
                         preview: bool, duration: float, end_tolerance: float,
                         max_line: int, source: str, encoding: str,
                         name_format: str = core.DEFAULT_NAME_FORMAT,
                         preview_dir: Path | None = None) -> None:
        errors: list[str] = []
        results: list[dict] = []
        try:
            files = core.scan_audio(folder, errors, recursive=recursive, suffixes=formats)
            self.queue.put(("scan", len(files), list(errors)))
            for index, path in enumerate(files, 1):
                if self.cancel_event.is_set():
                    break
                self.queue.put(("current", path.name, index, len(files)))
                try:
                    result = core.process_file(
                        path, encoding, preview, 15, source=source, prefer=prefer,
                        convert=convert, max_line_chars=max_line, drop_credits=credits,
                        overwrite=overwrite, duration_tolerance=duration,
                        end_time_tolerance=end_tolerance, name_format=name_format,
                        preview_dir=preview_dir)
                except Exception as exc:  # 单首异常不应中断整批
                    result = {"status": "error", "audio": str(path),
                              "message": f"处理失败: {type(exc).__name__}: {exc}"}
                results.append(result)
                self.queue.put(("result", result, index, len(files)))
            self.queue.put(("done", results, list(errors), self.cancel_event.is_set()))
        except Exception as exc:
            self.queue.put(("fatal", f"扫描失败: {type(exc).__name__}: {exc}"))

    def _cancel(self) -> None:
        if not self.running:
            return
        self.cancel_event.set()
        self._log("warn", "已请求取消；当前文件完成后停止。")
        self.cancel_button.configure(state="disabled")

    # ------------------------------------------------------------
    # 删除歌词
    # ------------------------------------------------------------
    def _delete_lrc(self) -> None:
        if self.running:
            return
        folder = Path(self.folder_var.get().strip())
        if not folder.is_dir():
            messagebox.showerror(APP_TITLE, "请选择有效的音乐目录。")
            return
        try:
            files = find_lrc_files(folder, self.recursive_var.get())
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"扫描歌词失败：{exc}")
            return
        if not files:
            messagebox.showinfo(APP_TITLE, "没有找到 .lrc 文件。")
            return
        if not messagebox.askyesno(
                APP_TITLE,
                f"在 {folder} 中找到 {len(files)} 个 .lrc 文件。\n\n"
                "将移到 Windows 回收站（失败时移到 _lrc_backup 目录）。\n"
                "不会删除音频文件。是否继续？"):
            return
        self._save_settings()
        self._set_running(True, "delete")
        self.cancel_event.clear()
        self._log("warn", f"准备删除 {len(files)} 个歌词文件…")
        self.worker = threading.Thread(target=self._delete_worker,
                                       args=(folder, files), daemon=True)
        self.worker.start()

    def _delete_worker(self, folder: Path, files: list[Path]) -> None:
        try:
            ok = send_to_recycle_bin([str(item) for item in files])
            moved = 0
            failed = []
            if not ok:
                backup = folder / f"_lrc_backup_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                for path in files:
                    if self.cancel_event.is_set():
                        break
                    try:
                        relative = path.relative_to(folder)
                        destination = backup / relative
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(path), str(destination))
                        moved += 1
                    except OSError:
                        failed.append(str(path))
                self.queue.put(("delete_done", moved, failed, str(backup)))
            else:
                self.queue.put(("delete_done", len(files), [], ""))
        except Exception as exc:
            self.queue.put(("delete_done", 0, [str(exc)], ""))

    # ------------------------------------------------------------
    # 报告
    # ------------------------------------------------------------
    def _save_report(self) -> None:
        if not self.results:
            messagebox.showinfo(APP_TITLE, "还没有可保存的结果。")
            return
        name = f"lrc-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        target = filedialog.asksaveasfilename(
            title="保存 JSON 报告", defaultextension=".json",
            initialfile=name, filetypes=[("JSON", "*.json")])
        if not target:
            return
        summary = dict(Counter(item.get("status", "unknown") for item in self.results))
        report = {
            "created_at": datetime.now().astimezone().isoformat(),
            "root": self.folder_var.get().strip(),
            "summary": summary,
            "results": self.results,
        }
        try:
            Path(target).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8")
            self._log("info", f"报告已保存：{target}")
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"报告保存失败：{exc}")

    # ------------------------------------------------------------
    # 队列/结果
    # ------------------------------------------------------------
    def _poll_queue(self) -> None:
        if self._closing:
            return
        try:
            while True:
                message = self.queue.get_nowait()
                kind = message[0]
                if kind == "scan":
                    self._on_scan(message[1], message[2])
                elif kind == "current":
                    self._on_current(message[1], message[2], message[3])
                elif kind == "result":
                    self._on_result(message[1], message[2], message[3])
                elif kind == "done":
                    self._on_done(message[1], message[2], message[3])
                elif kind == "fatal":
                    self._on_fatal(message[1])
                elif kind == "delete_done":
                    self._on_delete_done(message[1], message[2], message[3])
                elif kind == "update":
                    self._on_update(message[1], message[2])
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _on_scan(self, total: int, errors: list[str]) -> None:
        self.total = total
        self.progress.configure(maximum=max(1, total), value=0)
        self.progress_label.configure(text=f"0 / {total}")
        self._log("info", f"扫描完成：{total} 个音频文件。")
        for error in errors:
            self._log("warn", f"扫描警告：{error}")

    def _on_current(self, name: str, index: int, total: int) -> None:
        self.progress_label.configure(text=f"{index - 1} / {total}")
        self._log("info", f"当前正在生成 {name} 歌词中…")

    def _on_result(self, result: dict, index: int, total: int) -> None:
        name = Path(result.get("audio", "")).name
        status = result.get("status", "error")
        self.summary[status] += 1
        if status == "saved":
            suffix = "（覆盖）" if result.get("overwritten") else ""
            self._log("success", f"{name} 生成成功{suffix}")
            self._log_result_detail(result)
        elif status == "dry_run":
            self._log("success", f"{name} 预览完成（未写文件）")
            self._log_result_detail(result)
            if result.get("preview_path"):
                self._log_link(
                    f"  预览文件：{Path(result['preview_path']).name}（点击用记事本打开）",
                    result["preview_path"], self._open_preview_file)
            if result.get("preview_error"):
                self._log("warn", f"  {result['preview_error']}")
        elif status == "exists":
            self._log("warn", f"{name} 已存在，跳过（不覆盖）")
        else:
            self._log("error", f"{name} 生成失败：{result.get('message', '未知错误')}")
            self._log_result_detail(result)
        self.progress.configure(value=index)
        self.progress_label.configure(text=f"{index} / {total}")

    def _log_result_detail(self, result: dict) -> None:
        selected = result.get("selected")
        if selected:
            self._log(
                "muted",
                f"  来源 {selected.get('provider')}  ID={selected.get('id')}  "
                f"时长差={selected.get('duration_delta', 0):.3f}s  "
                f"专辑匹配={'是' if selected.get('album_match') else '否'}  "
                f"{result.get('line_count', 0)} 行")
        for warning in result.get("warnings", []):
            self._log("warn", f"  警告：{warning}")

    def _on_done(self, results: list[dict], errors: list[str], cancelled: bool) -> None:
        self.results = results
        saved = sum(1 for item in results if item.get("status") == "saved")
        previewed = sum(1 for item in results if item.get("status") == "dry_run")
        skipped = sum(1 for item in results if item.get("status") == "exists")
        failed = len(results) - saved - previewed - skipped
        self.report_button.configure(state="normal" if results else "disabled")
        self._set_running(False)
        self.progress.configure(value=self.total)
        self.progress_label.configure(text=f"{len(results)} / {self.total}")
        prefix = "已取消，" if cancelled else ""
        parts = [f"成功 {saved}"]
        if previewed:
            parts.append(f"预览 {previewed}（未写文件）")
        parts.extend([f"跳过 {skipped}", f"失败 {failed}"])
        summary = prefix + "，".join(parts)
        self.summary_label.configure(text=f"完成：{summary}")
        self._log("summary", f"全部完成：{summary}")
        if previewed and self.preview_dir is not None:
            self._log_link(f"预览文件目录：{self.preview_dir}（点击打开）",
                           self.preview_dir, self._open_preview_dir)
        for error in errors:
            self._log("warn", f"扫描警告：{error}")
        self._save_settings()

    def _on_fatal(self, message: str) -> None:
        self._log("error", message)
        self._set_running(False)
        self.progress_label.configure(text="出错")
        self.summary_label.configure(text="任务失败")

    def _on_delete_done(self, count: int, failed: list[str], backup: str) -> None:
        self._set_running(False)
        if backup:
            self._log("success", f"已移动 {count} 个歌词到备份目录：{backup}")
        else:
            self._log("success", f"已移动 {count} 个歌词到回收站。")
        for item in failed:
            self._log("error", f"删除失败：{item}")
        self.summary_label.configure(text=f"删除完成：成功 {count}，失败 {len(failed)}")

    # ------------------------------------------------------------
    def _on_close(self) -> None:
        if self.running:
            if not messagebox.askyesno(APP_TITLE, "任务仍在运行，确定退出？"):
                return
            self.cancel_event.set()
        self._cleanup_previews()
        self._save_settings()
        self._closing = True
        self.root.destroy()


def _emit_line(text: str) -> None:
    """控制台可用时直接打印；windowed EXE 没有 stdout，退回消息框。"""
    stream = sys.stdout
    if stream is not None:
        try:
            stream.write(text + "\n")
            stream.flush()
            return
        except (OSError, ValueError):
            pass
    if getattr(sys, "frozen", False):
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo("Pebby Lyrics", text)
            root.destroy()
        except tk.TclError:
            pass


def _cli_action() -> "int | None":
    """处理 --version / --check-update；返回 None 表示正常启动界面。"""
    args = set(sys.argv[1:])
    if args & {"--version", "-V"}:
        _emit_line(f"Pebby Lyrics v{__version__}")
        return 0
    if "--check-update" in args:
        result = update_check.check()
        if result.get("status") == "update":
            _emit_line(f"发现新版本 v{result.get('version')}（当前 v{__version__}）")
            _emit_line(str(result.get("url") or update_check.RELEASES_PAGE))
            return 0
        if result.get("status") == "latest":
            _emit_line(f"已是最新版本 v{__version__}")
            return 0
        _emit_line(f"检查更新失败：{result.get('message', '未知错误')}")
        return 1
    return None


def main() -> int:
    action = _cli_action()
    if action is not None:
        return action
    try:
        if os.name == "nt":
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    try:
        root = tk.Tk()
        LrcApp(root)
        root.mainloop()
    except Exception:
        try:
            log_dir = app_data_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "gui_error.log").write_text(traceback.format_exc(), encoding="utf-8")
        except Exception:
            pass
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
