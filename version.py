# -*- coding: utf-8 -*-
"""バージョン情報。パッチ更新システムはこの値をリモートと比較する。"""

__version__ = "1.0.1"
APP_NAME = "AppLauncher"
APP_TITLE = "アプリランチャー"

# パッチ更新の配布元。GitHub Releases の latest を参照する。
GITHUB_OWNER = "kngmincorp-stack"
GITHUB_REPO = "app-launcher"
UPDATE_API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
