# -*- coding: utf-8 -*-
"""アプリランチャー (App Launcher)

登録した exe をパネルとして並べ、クリックで起動できる統合ランチャー。
・パネルは起動中=薄緑「● 起動中」/ 停止中=グレー「■ 停止中」を常時表示（2秒毎更新）
・×ボタンで終了せずタスクトレイに常駐（トレイの「終了」で完全終了）
・Windows スタートアップ登録（起動時もウィンドウを表示したまま開始）
・パッチ更新システム（GitHub Releases 参照）
"""
import os
import sys
import time
import ctypes
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import config
import procmon
import startup
import updater
from version import __version__, APP_NAME, APP_TITLE, UPDATE_API_URL

try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except Exception:
    HAS_TRAY = False

POLL_MS = 2000          # 起動状態の更新間隔
COLS = 2                # パネルの列数

# 配色
C_RUN_BG = "#d3f2d3"    # 薄緑（起動中）
C_RUN_EDGE = "#5cb85c"
C_RUN_FG = "#1e7a1e"
C_STOP_BG = "#ececec"   # グレー（停止中）
C_STOP_EDGE = "#bbbbbb"
C_STOP_FG = "#888888"
C_MISS_BG = "#f7e3e3"   # exe が見つからない
C_MISS_FG = "#b04040"


class AppEditDialog(tk.Toplevel):
    """パネル（アプリ登録）の追加・編集ダイアログ。"""

    def __init__(self, parent, title, name="", path=""):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.result = None

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="ソフト名").grid(row=0, column=0, sticky="w", pady=4)
        self.name_var = tk.StringVar(value=name)
        ttk.Entry(body, textvariable=self.name_var, width=40).grid(
            row=0, column=1, columnspan=2, sticky="we", padx=(8, 0), pady=4)

        ttk.Label(body, text="exe ファイル").grid(row=1, column=0, sticky="w", pady=4)
        self.path_var = tk.StringVar(value=path)
        ttk.Entry(body, textvariable=self.path_var, width=40).grid(
            row=1, column=1, sticky="we", padx=(8, 6), pady=4)
        ttk.Button(body, text="参照…", command=self._browse).grid(row=1, column=2, pady=4)

        btns = ttk.Frame(body)
        btns.grid(row=2, column=0, columnspan=3, pady=(12, 0))
        ttk.Button(btns, text="OK", width=10, command=self._ok).pack(side="left", padx=6)
        ttk.Button(btns, text="キャンセル", width=10, command=self.destroy).pack(side="left", padx=6)

        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(50, self.focus_force)

    def _browse(self):
        p = filedialog.askopenfilename(
            parent=self,
            title="起動する exe を選択",
            filetypes=[("実行ファイル", "*.exe"), ("すべてのファイル", "*.*")],
            initialdir=os.path.dirname(self.path_var.get()) or None,
        )
        if p:
            self.path_var.set(p.replace("/", "\\"))
            if not self.name_var.get().strip():
                base = os.path.splitext(os.path.basename(p))[0]
                self.name_var.set(base)

    def _ok(self):
        name = self.name_var.get().strip()
        path = self.path_var.get().strip()
        if not name or not path:
            messagebox.showwarning("入力不足", "ソフト名と exe ファイルの両方を入力してください。",
                                   parent=self)
            return
        self.result = {"name": name, "path": path}
        self.destroy()


class App(tk.Tk):
    def __init__(self, autostart=False):
        super().__init__()
        self.title(f"{APP_TITLE}  v{__version__}")
        self.minsize(560, 360)
        self.geometry("620x480")

        self.cfg = config.load()
        self.panels = []          # [(frame, name_label, status_label, sub_label)]
        self._tray_icon = None
        self._quitting = False
        self._scan_busy = False
        self._running = set()
        self._scan_queue = queue.Queue()
        self._ui_queue = queue.Queue()      # ワーカースレッド→UI へのコールバック
        self._recent_launch = {}            # パス -> 最終起動時刻（二重起動デバウンス）

        # スタートアップ自己修復（設定で有効なら起動のたびに登録を検証・再登録）
        self._startup_heal_msg = None
        if self.cfg.get("startup"):
            try:
                self._startup_heal_msg = startup.heal()
            except Exception as e:
                self._startup_heal_msg = f"自己修復に失敗しました: {e}"
        self.startup_var = tk.BooleanVar(value=startup.is_enabled())

        self._build_ui()
        self._rebuild_panels()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._create_tray_icon()
        self.after(200, self._poll)
        self.after(150, self._drain_scans)

        # スタートアップ起動時（--autostart）もウィンドウを表示したまま起動する
        # （トレイ格納は×ボタンを押した時のみ）

    # ---------- UI ----------
    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        header = ttk.Frame(self)
        header.pack(fill="x", padx=14, pady=(12, 4))
        ttk.Label(header, text=APP_TITLE, font=("Meiryo UI", 15, "bold")).pack(side="left")
        ttk.Label(header, text="パネルをクリックすると起動します。右クリックで編集・削除。",
                  foreground="#555").pack(side="left", padx=(14, 0), pady=(6, 0))

        # パネル領域（スクロール可能）
        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True, padx=14, pady=6)
        self.canvas = tk.Canvas(wrap, highlightthickness=0, bg=self.cget("bg"))
        self.canvas.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(wrap, command=self.canvas.yview)
        sb.pack(side="right", fill="y")
        self.canvas.config(yscrollcommand=sb.set)
        self.panel_area = tk.Frame(self.canvas, bg=self.cget("bg"))
        self._canvas_win = self.canvas.create_window((0, 0), window=self.panel_area, anchor="nw")
        self.panel_area.bind(
            "<Configure>",
            lambda e: self.canvas.config(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfigure(self._canvas_win, width=e.width))
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        # 下部バー
        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=14, pady=(2, 10))
        ttk.Button(bottom, text="＋ アプリを追加", command=self._add_app).pack(side="left")
        ttk.Checkbutton(
            bottom,
            text="Windows スタートアップに登録（PC 起動時に自動実行）",
            variable=self.startup_var,
            command=self._toggle_startup,
        ).pack(side="left", padx=12)
        ttk.Button(bottom, text="更新を確認", command=self._check_update).pack(side="right")

        self.status_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.status_var, foreground="#666").pack(
            anchor="w", padx=16, pady=(0, 6))
        if self._startup_heal_msg:
            self.status_var.set(f"[スタートアップ] {self._startup_heal_msg}")

    def _on_mousewheel(self, event):
        # パネル領域の上にポインタがある時だけスクロール（ダイアログ等では無効）
        try:
            w = self.winfo_containing(event.x_root, event.y_root)
        except (KeyError, tk.TclError):
            return
        while w is not None:
            if w is self.canvas:
                self.canvas.yview_scroll(-1 * (event.delta // 120), "units")
                return
            w = getattr(w, "master", None)

    def _rebuild_panels(self):
        # 再構築前に届いていた古いスキャン結果は破棄する（旧パネル向けのため）
        try:
            while True:
                self._scan_queue.get_nowait()
        except queue.Empty:
            pass
        for child in self.panel_area.winfo_children():
            child.destroy()
        self.panels = []
        apps = self.cfg.get("apps", [])

        for c in range(COLS):
            self.panel_area.grid_columnconfigure(c, weight=1, uniform="panel")

        if not apps:
            hint = tk.Label(
                self.panel_area,
                text="登録されたアプリがありません。\n下の「＋ アプリを追加」から exe を登録してください。",
                bg=self.cget("bg"), fg="#777", font=("Meiryo UI", 10), justify="center",
                pady=40)
            hint.grid(row=0, column=0, columnspan=COLS, sticky="nsew")
            return

        for i, app in enumerate(apps):
            frame = tk.Frame(self.panel_area, bg=C_STOP_BG, bd=0, relief="flat",
                             highlightthickness=2, highlightbackground=C_STOP_EDGE,
                             cursor="hand2")
            frame.grid(row=i // COLS, column=i % COLS, sticky="nsew", padx=6, pady=6)

            name_lbl = tk.Label(frame, text=app["name"], bg=C_STOP_BG,
                                font=("Meiryo UI", 13, "bold"), anchor="w", cursor="hand2")
            name_lbl.pack(fill="x", padx=14, pady=(12, 0))
            status_lbl = tk.Label(frame, text="■ 停止中", bg=C_STOP_BG, fg=C_STOP_FG,
                                  font=("Meiryo UI", 11, "bold"), anchor="w", cursor="hand2")
            status_lbl.pack(fill="x", padx=14, pady=(2, 0))
            sub_lbl = tk.Label(frame, text=os.path.basename(app["path"]), bg=C_STOP_BG,
                               fg="#999999", font=("Meiryo UI", 8), anchor="w", cursor="hand2")
            sub_lbl.pack(fill="x", padx=14, pady=(0, 10))

            widgets = (frame, name_lbl, status_lbl, sub_lbl)
            for w in widgets:
                w.bind("<Button-1>", lambda e, idx=i: self._on_panel_click(idx))
                w.bind("<Button-3>", lambda e, idx=i: self._show_context_menu(e, idx))
            self.panels.append(widgets)

        self._refresh_status(force=True)

    # ---------- 起動状態ポーリング ----------
    # プロセススキャンは数百 ms かかるため UI スレッドでは行わず、
    # バックグラウンドスレッドで実行して結果だけ after() で反映する。
    def _poll(self):
        if self._quitting:
            return
        self._refresh_status()
        self.after(POLL_MS, self._poll)

    def _refresh_status(self, force=False):
        if self._scan_busy and not force:
            return
        self._scan_busy = True
        paths = [a["path"] for a in self.cfg.get("apps", [])]

        def worker():
            try:
                running = procmon.running_set(paths)
            except Exception:
                running = None
            finally:
                self._scan_busy = False
            if running is not None:
                # UI 反映はキュー経由（別スレッドから Tk を直接触らない）
                self._scan_queue.put(running)

        threading.Thread(target=worker, daemon=True).start()

    def _drain_scans(self):
        if self._quitting:
            return
        try:
            while True:
                running = self._scan_queue.get_nowait()
                self._apply_status(running)
        except queue.Empty:
            pass
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                fn()
        except queue.Empty:
            pass
        self.after(150, self._drain_scans)

    def _apply_status(self, running):
        apps = self.cfg.get("apps", [])
        if not apps or len(self.panels) != len(apps):
            return
        self._running = running
        for i, app in enumerate(apps):
            frame, name_lbl, status_lbl, sub_lbl = self.panels[i]
            missing = not os.path.isfile(app["path"])
            if app["path"] in running:
                bg, edge = C_RUN_BG, C_RUN_EDGE
                status_lbl.config(text="● 起動中", fg=C_RUN_FG)
            elif missing:
                bg, edge = C_MISS_BG, C_MISS_FG
                status_lbl.config(text="× exe が見つかりません", fg=C_MISS_FG)
            else:
                bg, edge = C_STOP_BG, C_STOP_EDGE
                status_lbl.config(text="■ 停止中", fg=C_STOP_FG)
            frame.config(bg=bg, highlightbackground=edge)
            for w in (name_lbl, status_lbl, sub_lbl):
                w.config(bg=bg)

    # ---------- パネル操作 ----------
    def _on_panel_click(self, idx):
        apps = self.cfg.get("apps", [])
        if idx >= len(apps):
            return
        app = apps[idx]
        path = app["path"]
        if getattr(self, "_running", None) and path in self._running:
            # 起動中のパネルをクリック → 該当ソフトのウィンドウを前面に出す
            # （最小化なら復元、タスクトレイ格納中なら表示して前面化）
            name = app["name"]
            self.status_var.set(f"「{name}」を前面に表示しています…")

            def worker():
                try:
                    ok = procmon.bring_to_front(path)
                except Exception:
                    ok = False
                msg = (f"「{name}」を前面に表示しました。" if ok else
                       f"「{name}」のウィンドウが見つかりません。"
                       "タスクトレイのアイコンから開いてください。")
                self._ui_queue.put(lambda: self.status_var.set(msg))

            threading.Thread(target=worker, daemon=True).start()
            return
        if not os.path.isfile(path):
            messagebox.showerror(
                "起動できません",
                f"exe が見つかりません:\n{path}\n\n右クリック → 編集 でパスを直してください。")
            return
        # ダブルクリック等の連打による二重起動を防ぐ（状態反映は最大2秒遅れるため）
        now = time.monotonic()
        if now - self._recent_launch.get(path, -999.0) < 3.0:
            return
        self._recent_launch[path] = now
        try:
            subprocess.Popen(
                [path],
                cwd=os.path.dirname(path) or None,
                creationflags=(subprocess.DETACHED_PROCESS
                               | subprocess.CREATE_NEW_PROCESS_GROUP),
                close_fds=True,
            )
            self.status_var.set(f"「{app['name']}」を起動しました。")
        except OSError as e:
            messagebox.showerror("起動失敗", f"「{app['name']}」を起動できませんでした:\n{e}")
            return
        # 起動反映を早めに拾う
        self.after(700, self._refresh_status)
        self.after(1800, self._refresh_status)

    def _show_context_menu(self, event, idx):
        apps = self.cfg.get("apps", [])
        is_running = idx < len(apps) and apps[idx]["path"] in self._running
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="起動", command=lambda: self._on_panel_click(idx),
                         state="disabled" if is_running else "normal")
        menu.add_command(label="停止", command=lambda: self._stop_app(idx),
                         state="normal" if is_running else "disabled")
        menu.add_separator()
        menu.add_command(label="編集…", command=lambda: self._edit_app(idx))
        menu.add_command(label="削除", command=lambda: self._delete_app(idx))
        menu.add_separator()
        menu.add_command(label="上へ", command=lambda: self._move_app(idx, -1))
        menu.add_command(label="下へ", command=lambda: self._move_app(idx, +1))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _stop_app(self, idx):
        apps = self.cfg.get("apps", [])
        if idx >= len(apps):
            return
        app = apps[idx]
        if not messagebox.askyesno(
                "停止の確認",
                f"「{app['name']}」を停止しますか？\n\n"
                "プロセスを強制終了します。処理中のデータがある場合は\n"
                "先にソフト側で作業を終えてから停止してください。"):
            return
        path = app["path"]
        self.status_var.set(f"「{app['name']}」を停止しています…")

        def worker():
            killed, failed = procmon.terminate(path)
            self._ui_queue.put(lambda: self._apply_stop_result(app["name"], path,
                                                              killed, failed))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_stop_result(self, name, path, killed, failed):
        if killed and not failed:
            self.status_var.set(f"「{name}」を停止しました。")
        elif killed:
            self.status_var.set(f"「{name}」を停止しました（一部のプロセスを終了できませんでした）。")
        elif failed:
            messagebox.showwarning(
                "停止できませんでした",
                f"「{name}」のプロセスを終了できませんでした。\n"
                "管理者権限で動作している可能性があります。")
        else:
            self.status_var.set(f"「{name}」は起動していませんでした。")
        # 停止直後にすぐ再起動できるよう起動デバウンスを解除し、表示を更新する
        self._recent_launch.pop(path, None)
        self._refresh_status(force=True)

    def _add_app(self):
        dlg = AppEditDialog(self, "アプリを追加")
        self.wait_window(dlg)
        if dlg.result:
            self.cfg["apps"].append(dlg.result)
            config.save(self.cfg)
            self._rebuild_panels()

    def _edit_app(self, idx):
        apps = self.cfg.get("apps", [])
        if idx >= len(apps):
            return
        dlg = AppEditDialog(self, "アプリを編集",
                            name=apps[idx]["name"], path=apps[idx]["path"])
        self.wait_window(dlg)
        if dlg.result:
            apps[idx] = dlg.result
            config.save(self.cfg)
            self._rebuild_panels()

    def _delete_app(self, idx):
        apps = self.cfg.get("apps", [])
        if idx >= len(apps):
            return
        if not messagebox.askyesno("削除の確認",
                                   f"「{apps[idx]['name']}」をパネルから削除しますか？\n"
                                   "（ソフト本体は削除されません）"):
            return
        del apps[idx]
        config.save(self.cfg)
        self._rebuild_panels()

    def _move_app(self, idx, delta):
        apps = self.cfg.get("apps", [])
        j = idx + delta
        if 0 <= idx < len(apps) and 0 <= j < len(apps):
            apps[idx], apps[j] = apps[j], apps[idx]
            config.save(self.cfg)
            self._rebuild_panels()

    # ---------- スタートアップ ----------
    def _toggle_startup(self):
        try:
            startup.set_enabled(self.startup_var.get())
            self.cfg["startup"] = self.startup_var.get()
            config.save(self.cfg)
            if self.startup_var.get():
                self.status_var.set("Windows スタートアップに登録しました。")
            else:
                self.status_var.set("Windows スタートアップ登録を解除しました。")
        except Exception as e:
            messagebox.showerror("エラー", f"スタートアップ設定に失敗しました:\n{e}")
            self.startup_var.set(startup.is_enabled())

    # ---------- 更新 ----------
    def _check_update(self):
        self.status_var.set("更新を確認しています…")
        threading.Thread(target=self._check_update_worker, daemon=True).start()

    def _check_update_worker(self):
        info = updater.check_latest()
        if not info:
            reason = updater.LAST_ERROR or "不明なエラー"
            self.after(0, lambda: (
                self.status_var.set(f"[更新] 取得失敗: {reason}"),
                messagebox.showwarning("更新確認",
                                       f"更新情報を取得できませんでした。\n\n理由: {reason}\n"
                                       f"参照先: {UPDATE_API_URL}")))
            return
        if not updater.is_newer(info["version"]):
            self.after(0, lambda: (
                self.status_var.set(f"[更新] 最新版です（現行 v{__version__}）。"),
                messagebox.showinfo("更新確認",
                                    f"お使いのバージョンは最新です。\n現行: v{__version__}")))
            return
        self.after(0, lambda: self._prompt_update(info))

    def _prompt_update(self, info):
        msg = (f"新しいバージョン v{info['version']} が見つかりました。\n"
               f"現行: v{__version__}\n\n更新しますか？（更新後に再起動します）")
        if not messagebox.askyesno("更新があります", msg):
            return
        self.status_var.set("[更新] ダウンロード中…")

        def worker():
            ok, detail = updater.download_and_apply(info["url"])
            self._ui_queue.put(lambda: self._apply_update_result(ok, detail))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_update_result(self, ok, detail):
        self.status_var.set(f"[更新] {detail}")
        if ok:
            self.after(500, self._quit_app)
        else:
            messagebox.showwarning("更新", detail)

    # ---------- トレイ常駐 ----------
    def _tray_image(self):
        img = Image.new("RGB", (64, 64), "#2d6cdf")
        d = ImageDraw.Draw(img)
        # 2x2 のパネル風アイコン
        for x, y, color in ((8, 8, "#ffffff"), (36, 8, "#c8f0c8"),
                            (8, 36, "#c8f0c8"), (36, 36, "#ffffff")):
            d.rectangle((x, y, x + 20, y + 20), fill=color)
        return img

    def _create_tray_icon(self):
        if not HAS_TRAY:
            return
        menu = pystray.Menu(
            pystray.MenuItem("開く", lambda: self.after(0, self._show_window), default=True),
            pystray.MenuItem("終了", lambda: self.after(0, self._quit_app)),
        )
        self._tray_icon = pystray.Icon(APP_TITLE, self._tray_image(), APP_TITLE, menu)

        def run_tray(icon=self._tray_icon):
            try:
                icon.run()
            except Exception:
                # トレイスレッドが死んだら×ボタンを「終了」に戻す
                # （withdraw すると開く手段が無くなるため）
                self._tray_icon = None

        threading.Thread(target=run_tray, daemon=True).start()

    def _show_window(self):
        self.deiconify()
        self.lift()
        try:
            self.focus_force()
        except tk.TclError:
            pass

    def _on_close(self):
        if HAS_TRAY and self._tray_icon:
            # ×では終了せずトレイへ（監視表示は継続）
            self.withdraw()
        else:
            self._quit_app()

    def _quit_app(self):
        if self._quitting:
            return
        self._quitting = True
        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        self.destroy()


def _selftest_update():
    """更新参照の自己診断。結果を %TEMP%\\al_selftest.txt に書き出して終了。"""
    import tempfile
    path = os.path.join(tempfile.gettempdir(), "al_selftest.txt")
    try:
        import certifi
        ca = certifi.where()
        ca_info = f"{ca} (exists={os.path.isfile(ca)}, frozen={getattr(sys, 'frozen', False)})"
    except Exception as e:
        ca_info = f"certifi 読込失敗: {e}"
    info = updater.check_latest()
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"version={__version__}\n")
        f.write(f"api={UPDATE_API_URL}\n")
        f.write(f"certifi={ca_info}\n")
        if info:
            f.write(f"result=OK remote={info['version']}\n")
        else:
            f.write(f"result=FAIL error={updater.LAST_ERROR}\n")


def _selftest_proc():
    """プロセス検出の自己診断。常駐している explorer.exe を検出できるかを確認する。"""
    import tempfile
    path = os.path.join(tempfile.gettempdir(), "al_proc_selftest.txt")
    windir = os.environ.get("WINDIR", r"C:\Windows")
    explorer = os.path.join(windir, "explorer.exe")
    ok = False
    err = ""
    try:
        ok = explorer in procmon.running_set([explorer])
    except Exception as e:
        err = str(e)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"tray={HAS_TRAY}\n")
        f.write(f"detected={ok}\n")
        if err:
            f.write(f"error={err}\n")


def _acquire_single_instance() -> bool:
    """名前付きミューテックスで二重起動を防止。既に起動済みなら False。"""
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW(None, False, f"Local\\{APP_NAME}_single_instance")
    return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def main():
    if "--selftest-update" in sys.argv:
        _selftest_update()
        return
    if "--selftest-proc" in sys.argv:
        _selftest_proc()
        return
    autostart = "--autostart" in sys.argv
    if not _acquire_single_instance():
        if not autostart:
            ctypes.windll.user32.MessageBoxW(
                None,
                f"{APP_TITLE}は既に起動しています。\nタスクトレイのアイコンから開いてください。",
                APP_TITLE, 0x40)
        return
    app = App(autostart=autostart)
    app.mainloop()


if __name__ == "__main__":
    main()
