# 台本から授業動画つくるマシーン v0.1

台本TXTからNotebookLM動画を生成・回収し、音声末尾処理、固定Ending付与、HLS変換、ZIP化までをジョブ単位で実行するWindowsデスクトップアプリです。

GNBCreator / ドウガッチンガー / HLS Converter の3エンジン構成<br>
Created by 福ゼミ塾長

## 現在の開発段階

Unit 0（開発基盤・元エンジン調査・統合設計）です。実サイトを操作するエンジン本体やFFmpeg変換本体はまだ統合していません。

このリポジトリではDBを使用しません。設定、キュー、状態、ジョブはアプリ配置フォルダ内のJSONへ原子的に保存する設計です。生成動画、ブラウザプロファイル、Cookie、ログ、秘密情報はGit管理対象外です。

## 開発環境

- Windows 10/11
- Python 3.11以上
- FFmpeg / ffprobe（次Unit以降のメディア処理で使用）
- Google Chrome（次Unit以降のNotebookLM操作で使用）

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e ".[dev]"
py -m pytest
```

## ディレクトリ

```text
input/       台本TXT
raw_files/   回収済み未編集MP4（上書き・自動削除禁止）
output/      完成ZIP
work/        ジョブ別の一時成果物
system/      settings.json / queue.json / state.json / jobs/*.json
logs/        実行ログ
browser/     専用ブラウザプロファイル
src/         アプリケーションコード
tests/       自動テスト
docs/        調査・設計資料
```

空ディレクトリのみ `.gitkeep` で保持し、実データは除外します。

## Unit 0資料

- [開発ルール](docs/DEVELOPMENT_RULES.md)
- [3エンジン調査](docs/engine-audit.md)
- [統合アーキテクチャ](docs/architecture.md)
- [GUI設計](docs/gui-design.md)
- [テスト戦略](docs/test-strategy.md)
- [次Unitの推奨分割](docs/unit1-plan.md)

## 安全原則

- RAW動画を後工程で上書きまたは自動削除しない。
- ダウンロードとRAWコピーの全検証が成功するまでNotebookLM側の動画を削除しない。
- `.crdownload`、一時ダウンロード、0 byte、ffprobe不合格ファイルを正常扱いしない。
- 旧仕様の「末尾を固定4秒削除」は使用しない。最終音声位置 + 0.5秒を終了点とする。
- CAPTCHA、本人確認、再ログイン、利用制限を迂回しない。
- 元3リポジトリを直接変更しない。
