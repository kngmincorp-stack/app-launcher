# -*- coding: utf-8 -*-
"""
パッチ更新システム。
GitHub Releases の latest を参照し、より新しいバージョンの .exe があれば
ダウンロードして自身を置き換える（再起動）。標準ライブラリのみで実装。
"""
import os
import sys
import json
import ssl
import tempfile
import subprocess
import urllib.request
import urllib.error

from version import __version__, UPDATE_API_URL, APP_NAME


def _ssl_context():
    """SSL コンテキストを作る。
    配布先PCにルート証明書が無くても検証できるよう、certifi 同梱の CA バンドルを使う。
    （CERTIFICATE_VERIFY_FAILED 対策。certifi が無ければ OS 既定にフォールバック。）"""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _version_tuple(v: str):
    """'1.2.3' -> (1, 2, 3) 。比較用に数値化する。"""
    v = v.strip().lstrip("vV")
    parts = []
    for p in v.split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


# 直近の check_latest() で失敗した理由（診断用）。
LAST_ERROR = ""


def check_latest(timeout=10):
    """
    リモートの最新リリースを取得。
    戻り値: dict(version, url, notes) または None（取得失敗）。
    失敗理由は updater.LAST_ERROR に入る。
    """
    global LAST_ERROR
    LAST_ERROR = ""
    ctx = _ssl_context()
    req = urllib.request.Request(
        UPDATE_API_URL,
        headers={"User-Agent": APP_NAME, "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            LAST_ERROR = f"HTTP 404: リリースが見つかりません（{UPDATE_API_URL}）"
        else:
            LAST_ERROR = f"HTTP {e.code}: {e.reason}"
        return None
    except urllib.error.URLError as e:
        LAST_ERROR = f"通信エラー: {e.reason}（ネット接続/プロキシ/ファイアウォールを確認）"
        return None
    except Exception as e:
        LAST_ERROR = f"予期しないエラー: {e}"
        return None

    tag = data.get("tag_name") or data.get("name") or ""
    exe_url = None
    for asset in data.get("assets", []):
        name = (asset.get("name") or "").lower()
        if name.endswith(".exe"):
            exe_url = asset.get("browser_download_url")
            break
    if not tag:
        LAST_ERROR = "リリースにタグ名がありません。"
        return None
    if not exe_url:
        LAST_ERROR = "リリースに .exe が添付されていません。"
        return None
    return {
        "version": tag.lstrip("vV"),
        "url": exe_url,
        "notes": data.get("body", "") or "",
    }


def is_newer(remote_version: str) -> bool:
    """リモート版が現行版より新しいか。"""
    return _version_tuple(remote_version) > _version_tuple(__version__)


def is_frozen() -> bool:
    """PyInstaller でビルドされた exe として動いているか。"""
    return getattr(sys, "frozen", False)


def download_and_apply(download_url: str, timeout=60, autostart=False):
    """
    新しい exe をダウンロードし、バッチ経由で自身を差し替えて再起動する。
    exe 実行時のみ有効（スクリプト実行時は False を返す）。
    autostart=True なら再起動後に自動で監視を開始する（--autostart 付きで起動）。
    """
    if not is_frozen():
        return False, "exe 実行時のみ自動更新できます（開発中はスキップ）。"

    current_exe = sys.executable  # 実行中の exe パス
    tmp_dir = tempfile.gettempdir()
    new_exe = os.path.join(tmp_dir, f"{APP_NAME}_new.exe")
    log_path = os.path.join(tmp_dir, f"{APP_NAME}_update.log")

    ctx = _ssl_context()
    req = urllib.request.Request(download_url, headers={"User-Agent": APP_NAME})
    expected = None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp, \
                open(new_exe, "wb") as f:
            cl = resp.headers.get("Content-Length")
            if cl and cl.isdigit():
                expected = int(cl)
            # 逐次書き込み＋ディスクへ確実にフラッシュ
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        return False, f"ダウンロード失敗: {e}"

    # ダウンロード結果を厳密に検証（不完全な exe を掴むと
    # 起動時に『Failed to load Python DLL / モジュールが見つかりません』になる）
    try:
        actual = os.path.getsize(new_exe)
        with open(new_exe, "rb") as f:
            head = f.read(2)
        if head != b"MZ":
            return False, "更新ファイルが不正です（exe ではありません）。再試行してください。"
        if actual < 1_000_000:
            return False, f"更新ファイルが小さすぎます（{actual} bytes）。再試行してください。"
        if expected is not None and actual != expected:
            return False, (f"ダウンロードが不完全です（{actual}/{expected} bytes）。"
                           "通信状況を確認して再試行してください。")
    except OSError as e:
        return False, f"更新ファイル検証失敗: {e}"

    # 実行中の exe はロックされているため、終了を待ってから差し替える必要がある。
    # バッチで「本体終了待ち → 上書き → 再起動」を行う。
    # 重要: 日本語(CP932)環境の cmd.exe に合わせ、バッチは ASCII コマンドのみ・
    #       ファイルはシステム既定コードページ(mbcs)で書き出す。
    #       （UTF-8+chcp だと日本語フォルダに置いた exe のパスが化けて
    #        「指定されたモジュールが見つかりません」になるため。）
    # onefile の exe は「親(bootloader)＋子(アプリ)」の 2 プロセスで動くため、
    # アプリ終了直後も一瞬 exe ファイルがロックされたままになる。
    # PID を待つのではなく、「置換に成功するまで move をリトライ」する方が確実。
    # 重要: onefile 実行中のプロセスは _PYI_APPLICATION_HOME_DIR 等の環境変数で
    #       「自分の展開先(_MEIxxxx)」を子プロセスへ伝えている。これが bat 経由で
    #       新 exe に継承されると、新 exe は旧 exe の展開フォルダ（終了時に削除済み）
    #       から python DLL を読もうとして『Failed to load Python DLL /
    #       指定されたモジュールが見つかりません』で起動に失敗する。
    #       → bat 内で明示的に消し、Popen にも除去済み環境を渡す（二重防御）。
    relaunch_args = " --autostart" if autostart else ""
    bat = os.path.join(tmp_dir, f"{APP_NAME}_update.bat")
    bat_content = (
        "@echo off\r\n"
        'set "_PYI_APPLICATION_HOME_DIR="\r\n'
        'set "_PYI_ARCHIVE_FILE="\r\n'
        'set "_PYI_PARENT_PROCESS_LEVEL="\r\n'
        'set "_MEIPASS2="\r\n'
        f'set "LOG={log_path}"\r\n'
        f'set "NEW={new_exe}"\r\n'
        f'set "DST={current_exe}"\r\n'
        '> "%LOG%" echo [update] start; waiting for file lock to release\r\n'
        "set /a tries=0\r\n"
        ":retry\r\n"
        'move /Y "%NEW%" "%DST%" >> "%LOG%" 2>&1\r\n'
        "if not errorlevel 1 goto done\r\n"
        "set /a tries+=1\r\n"
        'if %tries% GEQ 40 goto giveup\r\n'
        "ping -n 2 127.0.0.1 >nul\r\n"
        "goto retry\r\n"
        ":done\r\n"
        '>> "%LOG%" echo [update] replaced OK after %tries% retries; launching\r\n'
        f'start "" "{current_exe}"{relaunch_args}\r\n'
        '>> "%LOG%" echo [update] done\r\n'
        'del "%~f0"\r\n'
        "goto :eof\r\n"
        ":giveup\r\n"
        '>> "%LOG%" echo [update] FAILED: could not replace exe (still locked)\r\n'
        f'start "" "{current_exe}"{relaunch_args}\r\n'
        'del "%~f0"\r\n'
    )
    try:
        enc = "mbcs" if os.name == "nt" else "utf-8"
        with open(bat, "w", encoding=enc, errors="replace") as f:
            f.write(bat_content)
    except Exception as e:
        return False, f"更新スクリプト作成失敗: {e}"

    # PyInstaller が仕込んだ環境変数を除いたクリーンな環境で bat を起動する
    clean_env = {k: v for k, v in os.environ.items()
                 if not (k.startswith("_PYI") or k.startswith("_MEI"))}
    try:
        subprocess.Popen(
            ["cmd", "/c", bat],
            env=clean_env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except Exception as e:
        return False, f"更新プロセス起動失敗: {e}"
    return True, "更新を適用します。アプリを再起動します。"
