# Windows packaging preflight

対象指示ID: `DJD-CHAPPY-V01-UNIT3`

この文書は候補ビルドの事前検証手順です。正式release ZIPの作成や公開を許可するものではありません。

## 採用候補

PyInstaller 6系のwindowed `onedir` を採用します。`onefile` の起動時展開を避け、Qt plugin、Playwright、FFmpegの所在を利用者が確認でき、起動速度、ウイルス対策ソフトによる誤検知の抑制、障害診断、部分更新を優先します。実行ファイルは `DJDmaker.exe`、配布フォルダーは `DJDmaker_Ver1.1` です。

`packaging/DJDMaker.spec` は次を行います。

- PySide6 Widgets / Multimediaをhidden importし、PyInstallerのQt hookにplatform・multimedia plugin収集を任せる。
- Playwrightのdata、binary、hidden importを収集する。Playwright同梱Chromiumは配布せず、端末にインストール済みのGoogle Chromeを使う。
- `config/default-settings.json` を同梱する。
- 審査済み `ffmpeg.exe` と `ffprobe.exe` を `runtime/ffmpeg` に配置する。
- runtime hookで `runtime/ffmpeg` を子processの`PATH`先頭へ追加する。current working directoryは変更しない。
- PyInstaller収集時に`_internal`へ入るconfig、licenses、runtimeだけをbuild後にportable rootへ移し、root EXE＋`_internal`の構造を保つ。
- consoleを表示しない。UPXは使用しない。

FFmpeg binaryと対応licenseはrepositoryへコピーせず、ビルド時に以下を明示します。

```powershell
.\packaging\build_windows.ps1 `
  -FFmpegPath C:\reviewed\ffmpeg.exe `
  -FFprobePath C:\reviewed\ffprobe.exe `
  -FFmpegLicense C:\reviewed\FFmpeg-LICENSE.txt
```

このscriptはpreflight、onedir build、空の書込みdirectory作成、完成tree再検証までを行いますが、ZIP化、署名、push、公開は行いません。

## 配布時のdirectory方針

アプリ基準pathはcurrent working directoryやPyInstaller `_MEIPASS` ではなく、frozen時の `sys.executable` 親directoryです。次のdirectoryは配布root直下で書込み可能である必要があります。

`input / raw_files / output / work / system / system/jobs / logs / browser / browser/chrome-profile`

`Program Files`など通常ユーザーが書き込めない場所へ置かず、フォルダー全体を日本語や空白を含む書込み可能な場所へ展開します。Chrome profileにはログイン情報が含まれるため、releaseへ実データを入れません。利用開始後も配布や障害資料へprofile、Cookie、token、logs、台本、RAW、動画、ZIPを含めません。

## 手動preflight

```powershell
python -m djd_maker.packaging.preflight --root .
python -m djd_maker.packaging.preflight --root . --json
```

完成onedirには `--release-tree` を追加します。この検査はWindows、Python、PyInstaller、PySide6、Qt platform/multimedia plugin、Playwright、FFmpeg/ffprobe実行、Google Chrome、default config、書込みdirectory、日本語＋空白path、空のbrowser/log/system/media directory、必須release assetを検証します。

## 元3repositoryの確認結果

- AutoGeminiNoteBookCreatorはPyInstaller windowed onedir、`collect_all("playwright")`、外部指定した審査済みffprobe、portable root基準のChrome profile、allowlist型release tree検査を採用している。
- GeminiNotebookVideoMergeはwindowed onedir、外部指定FFmpeg/ffprobe、`runtime/ffmpeg`、license同梱、書込み可能なportable folder、日本語名EXEの実起動、packaged runtimeによる実FFmpeg試験を採用している。
- FukuzemiAppはPyInstaller onedir、root EXE＋`_internal`、FFmpeg/ffprobeのproject-local探索、ビルド後のroot EXE個数確認を採用している。

DJDmakerではこれらを統合し、PySide6固有のQt plugin検査、Playwrightと外部Chromeの分離、空profile、default JSON、Unicode/space path検査を追加しています。

正式配布前には別途、第三者ライセンス一式、Windows 10/11 clean machine、Chrome未ログイン時の導線、Qt Multimedia再生、実NotebookLM、実FFmpeg E2E、署名、ハッシュ、ZIP allowlistをrelease承認工程で確認してください。

実際の候補onedir buildとportable smokeの結果は `docs/unit4-packaging-verification.md` を参照してください。
