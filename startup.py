# -*- coding: utf-8 -*-
"""
Windows スタートアップ登録/解除。
HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run にエントリを作る。
管理者権限不要（ユーザー単位）。

Run エントリがあっても起動しないケースへの対策も持つ:
- タスクマネージャー「スタートアップ」タブで無効化されると
  StartupApproved\\Run に無効フラグ(先頭 0x03)が立ち、Windows は起動しない
  → enable() 時に有効フラグ(0x02)で上書きする。
- ブラウザでダウンロードした exe に残る Zone.Identifier (Mark of the Web) は
  ログオン時の自動起動を SmartScreen が無言でブロックすることがある
  → enable() 時に削除する。
- exe の移動/更新でエントリが古いパスを指したままになる
  → heal() で起動のたびに登録コマンドを検証・再登録する。
"""
import os
import sys
import winreg

from version import APP_NAME

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
# StartupApproved の「有効」を表す 12 バイト値（先頭 0x02、残り 0）
_APPROVED_ENABLED = bytes([0x02] + [0x00] * 11)


def _exe_command() -> str:
    """スタートアップに登録する起動コマンド。--autostart 付きで起動する。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --autostart'
    # 開発中(スクリプト実行)は pythonw + スクリプトパス（コンソールを出さない）
    py = sys.executable.replace("python.exe", "pythonw.exe")
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    return f'"{py}" "{script}" --autostart'


def registered_command():
    """Run キーに登録済みのコマンド文字列。未登録なら None。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, APP_NAME)
            return val
    except FileNotFoundError:
        return None
    except OSError:
        return None


def is_enabled() -> bool:
    return registered_command() is not None


def is_approved() -> bool:
    """タスクマネージャーの「スタートアップ」で無効化されていないか。
    値が無ければ有効扱い。先頭バイト 0x02 が有効、0x03 が無効。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _APPROVED_KEY, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, APP_NAME)
            return (not val) or val[0] == 0x02
    except FileNotFoundError:
        return True
    except OSError:
        return True


def _set_approved_enabled():
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _APPROVED_KEY) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_BINARY, _APPROVED_ENABLED)


def _clear_motw():
    """実行中 exe の Zone.Identifier (Mark of the Web) を削除する。"""
    if not getattr(sys, "frozen", False):
        return
    try:
        os.remove(sys.executable + ":Zone.Identifier")
    except OSError:
        pass  # 無ければそれで良い


def enable():
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _exe_command())
    _set_approved_enabled()
    _clear_motw()


def disable():
    for root_key, value_name in ((_RUN_KEY, APP_NAME), (_APPROVED_KEY, APP_NAME)):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, root_key, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, value_name)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def set_enabled(flag: bool):
    if flag:
        enable()
    else:
        disable()


def heal():
    """設定上スタートアップ有効なとき、起動のたびに登録状態を検証して直す。
    戻り値: 修復した内容の説明文。修復不要なら None。"""
    cmd = registered_command()
    expected = _exe_command()
    fixed = []
    if cmd is None:
        fixed.append("登録が消えていたため再登録しました")
    elif cmd != expected:
        fixed.append(f"登録先が古いパスでした（{cmd} → 現在の exe）ので書き直しました")
    if not is_approved():
        fixed.append("タスクマネージャーで無効化されていたため有効に戻しました")
    if fixed:
        enable()
        return "、".join(fixed) + "。"
    # 登録は正しい場合も MotW だけは毎回掃除しておく（初回 DL 直後対策）
    _clear_motw()
    return None
