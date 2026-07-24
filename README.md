# アプリランチャー (App Launcher)

複数の常駐ソフト（Cloud Printer / Gmail PNG Converter / ADF Scanner / フォルダ同期コピー等）を
1 画面のパネルから起動・状態確認できる統合ランチャー。

## 機能

- **パネル式ランチャー**: ソフト名と exe を登録するとパネルとして並ぶ。クリックで起動。
- **起動状態の常時表示**: 2 秒毎にプロセスを確認し、
  - 起動中 → パネルが**薄緑**になり「**● 起動中**」
  - 停止中 → グレーで「**■ 停止中**」
  - exe が見つからない → 薄赤で警告表示
- **二重起動防止**: 起動中のパネルをクリックしても再起動しない。
- **パネル管理**: 「＋ アプリを追加」で追加、右クリックで 編集 / 削除 / 並べ替え。
- **タスクトレイ常駐**: ×ボタンで終了せずトレイへ。トレイメニュー「開く / 終了」。
- **Windows スタートアップ登録**: チェック 1 つで PC 起動時に自動実行（トレイ常駐で開始）。
  登録消失・タスクマネージャー無効化・exe 移動は起動のたびに自己修復。
- **パッチ更新**: GitHub Releases (kngmincorp-stack/app-launcher) の latest を「更新を確認」で取得し自己置換。

## 起動状態の判定方式

Toolhelp32 スナップショットで全プロセスを列挙し、exe 名が一致したものだけ
フルパスを取得して登録パスと厳密比較（アクセス拒否時は exe 名一致にフォールバック）。
ランチャーから起動していないソフト（スタートアップ起動等）も正しく「起動中」になる。

## ファイル構成

| ファイル | 役割 |
|---------|------|
| `main.py` | メイン GUI（パネル・トレイ・更新） |
| `procmon.py` | プロセス検出（ctypes / Toolhelp32） |
| `config.py` | 設定 (`%APPDATA%\AppLauncher\config.json`) |
| `startup.py` | スタートアップ登録＋自己修復（folder-sync-copier 由来） |
| `updater.py` | GitHub Releases パッチ更新（folder-sync-copier 由来） |
| `build.bat` | exe ビルド → `dist\AppLauncher.exe` |
| `publish_patch.bat` | build → selftest → commit → push → gh release |

## 配布

`dist\AppLauncher.exe`（単一ファイル）を別 PC にコピーするだけ。設定は `%APPDATA%` に保存されるため exe の差し替え・更新で消えない。
