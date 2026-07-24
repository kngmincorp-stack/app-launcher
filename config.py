# -*- coding: utf-8 -*-
"""設定の読み書き。%APPDATA%\\AppLauncher\\config.json に保存する。

exe を更新・移動しても設定が消えないよう、exe と同じフォルダではなく
ユーザープロファイル配下に置く。
"""
import os
import json

from version import APP_NAME

_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
PATH = os.path.join(_DIR, "config.json")

DEFAULT = {
    # apps: [{"name": "表示名", "path": "C:\\...\\App.exe"}, ...]
    "apps": [],
    "startup": False,
}


def load() -> dict:
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return dict(DEFAULT)
    cfg = dict(DEFAULT)
    if isinstance(data, dict):
        cfg.update(data)
    if not isinstance(cfg.get("apps"), list):
        cfg["apps"] = []
    # 壊れたエントリ（手編集による型崩れ含む）は読み飛ばす
    cfg["apps"] = [a for a in cfg["apps"]
                   if isinstance(a, dict)
                   and isinstance(a.get("name"), str) and a["name"]
                   and isinstance(a.get("path"), str) and a["path"]]
    return cfg


def save(cfg: dict) -> None:
    os.makedirs(_DIR, exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PATH)
