# -*- coding: utf-8 -*-
"""実行中プロセスの検出（標準ライブラリ + ctypes のみ）。

Toolhelp32 スナップショットで全プロセスの (PID, exe名) を列挙し、
exe 名が一致した候補だけ QueryFullProcessImageNameW でフルパスを取得して
登録パスと厳密に比較する。フルパスが取れない（アクセス拒否等）場合は
exe 名一致をもって「起動中」とみなすフォールバックを行う。
"""
import os
import ctypes
from ctypes import wintypes

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
MAX_PATH_LONG = 32768


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


# restype/argtypes を明示する。特に HANDLE を restype 既定 (c_int) のままにすると
# 64bit で INVALID_HANDLE_VALUE (=2^64-1) との比較が永遠に成立しない。
_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_kernel32.Process32FirstW.restype = wintypes.BOOL
_kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
_kernel32.Process32NextW.restype = wintypes.BOOL
_kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.TerminateProcess.restype = wintypes.BOOL
_kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]


def _iter_processes():
    """(pid, exe名小文字) を列挙する。失敗時は空。"""
    snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not _kernel32.Process32FirstW(snap, ctypes.byref(entry)):
            return
        while True:
            yield entry.th32ProcessID, entry.szExeFile.lower()
            if not _kernel32.Process32NextW(snap, ctypes.byref(entry)):
                break
    finally:
        _kernel32.CloseHandle(snap)


def _full_path_of(pid: int):
    """PID のフルパス（小文字）。取得できなければ None。"""
    h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        size = wintypes.DWORD(MAX_PATH_LONG)
        buf = ctypes.create_unicode_buffer(size.value)
        if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.lower()
        return None
    finally:
        _kernel32.CloseHandle(h)


def running_set(target_paths):
    """登録パスのうち現在起動中のものを set で返す。

    target_paths: exe フルパスの iterable（大文字小文字は無視して比較）。
    """
    targets = {}  # basename小文字 -> [元のパス, ...]
    for p in target_paths:
        if not p:
            continue
        targets.setdefault(os.path.basename(p).lower(), []).append(p)
    if not targets:
        return set()

    running = set()
    resolved = set()    # フルパス取得に 1 件でも成功した basename
    unresolved = set()  # フルパス取得に失敗した PID を含む basename
    for pid, exe_name in _iter_processes():
        cands = targets.get(exe_name)
        if not cands:
            continue
        if all(c in running for c in cands):
            continue
        full = _full_path_of(pid)
        if full is None:
            unresolved.add(exe_name)
            continue
        resolved.add(exe_name)
        full_norm = os.path.normpath(full)
        for c in cands:
            if os.path.normpath(c.lower()) == full_norm:
                running.add(c)

    # フルパスが 1 件も取れなかった basename に限り、名前一致で起動中とみなす
    # （1 件でもパス比較できた basename は厳密比較の結果を信頼する）
    for name in unresolved - resolved:
        for c in targets[name]:
            running.add(c)
    return running


def terminate(target_path):
    """target_path（exe フルパス）に厳密一致するプロセスを全て強制終了する。

    表示用の running_set と違い、停止は誤爆が許されないため
    フルパスが一致したものだけを対象にする（名前一致フォールバック無し）。
    onefile PyInstaller アプリ（親+子の 2 プロセス）も両方止まる。
    戻り値: (終了させた数, 失敗した数)
    """
    if not target_path:
        return 0, 0
    base = os.path.basename(target_path).lower()
    want = os.path.normpath(target_path.lower())
    killed = failed = 0
    for pid, exe_name in _iter_processes():
        if exe_name != base:
            continue
        h = _kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE, False, pid)
        if not h:
            continue
        try:
            size = wintypes.DWORD(MAX_PATH_LONG)
            buf = ctypes.create_unicode_buffer(size.value)
            if not _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                continue
            if os.path.normpath(buf.value.lower()) != want:
                continue
            if _kernel32.TerminateProcess(h, 1):
                killed += 1
            else:
                failed += 1
        finally:
            _kernel32.CloseHandle(h)
    return killed, failed
