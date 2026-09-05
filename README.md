# 台本から授業動画つくるマシーン v0.1

台本TXTからNotebookLM動画を生成・回収し、音声末尾処理、固定Ending付与、HLS変換、ZIP化までをジョブ単位で実行するWindowsデスクトップアプリです。

GNBCreator / ドウガッチンガー / HLS Converter の3エンジン構成<br>
Created by 福ゼミ塾長

## Version

Version 0.1。Unit 0からUnit 4までの設計、実装、live acceptance、3-job soak、Windows portable検証を完了した正式リリースです。

PySide6 GUI、永続Notebook scheduler、非同期Pipeline、ジョブ詳細・ログ・再実行、Ending preview、専用Chrome profile、動画artifact限定Web削除、Fake Notebook E2Eを含みます。NotebookLMのlive acceptanceでは、動画回収、12項目のRAW安全gate、artifact限定削除、refresh後の非復活まで確認しています。

このリポジトリではDBを使用しません。設定、キュー、状態、ジョブはアプリ配置フォルダ内のJSONへ原子的に保存する設計です。生成動画、ブラウザプロファイル、Cookie、ログ、秘密情報はGit管理対象外です。

## 開発環境

- Windows 10/11
- Python 3.11以上
- FFmpeg / ffprobe
- Google Chrome（NotebookLM操作で使用）

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e ".[dev]"
py -m pytest
```

GUI起動:

```powershell
.\.venv\Scripts\djd-maker.exe
```

Portable版は`DJDmaker_v0.1`を任意の書き込み可能な場所へ展開し、`DJDmaker.exe`を起動します。Portable版にはFFmpeg / ffprobeが同梱されています。Windowsの保護機能が警告した場合は、入手元と公開SHA-256を確認してください。

初回は設定画面でEnding動画を指定してください。Gemini Notebookへのログインが必要な場合は、アプリが開く専用Chrome profile上で人間が操作します。CAPTCHAや認証を自動回避しません。

## 基本操作と自動処理

1. `input`へ台本TXTを配置し、設定画面で`raw_files`、`output`、Ending動画を指定します。
2. 必要な場合は専用ChromeでGoogleへログインします。CookieやpasswordをアプリやGitへ保存しません。
3. 開始すると、Notebook作成、rename、source投入、動画生成、scheduler監視、Downloadをjob単位で実行します。
4. DownloadしたMP4を検証して`raw_files`へ永久保存した後、動画artifactだけをWeb UIから削除します。Notebook本体とsourceは削除しません。
5. RAWにEnding処理を行い、6秒segmentのHLSと無圧縮ZIPを`output`へ作成します。

一時停止は新しい工程の開始を止め、保存済みdeadlineとjob状態を維持します。再開・アプリ再起動後も同一NotebookやDownloadを重複作成しない設計です。RAWは後工程で上書き・削除されません。

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
- [Unit 1実装記録](docs/unit1-implementation.md)
- [Unit 2実装記録](docs/unit2-implementation.md)
- [Unit 2エンジン忠実性補正](docs/unit2-engine-fidelity-patch.md)
- [Unit 3 Live Acceptance記録](docs/unit3-acceptance.md)
- [Unit 4 Multi-job Acceptance記録](docs/unit4-acceptance.md)
- [Unit 4 Portable検証](docs/unit4-packaging-verification.md)
- [Windows packaging preflight](docs/windows-packaging.md)

## Buildとライセンス

Windows portable buildは[Windows packaging手順](docs/windows-packaging.md)を参照してください。Buildには内容を確認したFFmpeg、ffprobe、および対応する`FFmpeg-LICENSE.txt`を明示指定します。配布folderにはPython/PySide6/Playwright等のruntimeと、各componentのライセンス情報が含まれます。FFmpegのライセンス条件は採用buildとcodec構成に依存するため、再build時に必ず再確認してください。

## 安全原則

- RAW動画を後工程で上書きまたは自動削除しない。
- ダウンロードとRAWコピーの全検証が成功するまでNotebookLM側の動画を削除しない。
- `.crdownload`、一時ダウンロード、0 byte、ffprobe不合格ファイルを正常扱いしない。
- 旧仕様の「末尾を固定4秒削除」は使用しない。最終音声位置 + 0.5秒を終了点とする。
- CAPTCHA、本人確認、再ログイン、利用制限を迂回しない。
- 元3リポジトリを直接変更しない。
