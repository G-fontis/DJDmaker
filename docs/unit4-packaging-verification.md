# Unit 4 Windows portable実機検証

指示ID: `DJD-CHAPPY-V01-UNIT4`

実施日: 2026-09-06（Asia/Tokyo）

正式release、ZIP、署名、commit、pushは実施していない。以下はGit管理外の候補onedirに対する検証結果である。

## Build

- Python: 3.14.6
- PyInstaller: 6.22.2
- PySide6: 6.11.2
- Playwright: 1.62.0
- FFmpeg / ffprobe: 9.0.1 full build
- OS: Windows 10 10.0.19045
- 出力: `C:\xampp\htdocs\PHP\DJDmaker\dist\DJDmaker_v0.1`
- EXE: `dist\DJDmaker_v0.1\DJDmaker.exe`
- 構成: 408 files、701,220,458 bytes（未ZIP）
- EXE SHA-256: `F56F9391098606DA4C913438D7FFBEDD396C8FEA9D94AC20B62C4B74A3C07A19`

build用venvは `build\packaging-venv` に`--system-site-packages`で作成し、repositoryを`--no-deps -e`で登録した。global PythonへPyInstallerは導入していない。

実行コマンドは次の形式。PowerShellの端末ポリシーを変更せず、当該processだけ`Bypass`した。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\packaging\build_windows.ps1 `
  -Python .\build\packaging-venv\Scripts\python.exe `
  -FFmpegPath <reviewed-ffmpeg.exe> `
  -FFprobePath <reviewed-ffprobe.exe> `
  -FFmpegLicense <matching-FFmpeg-LICENSE>
```

初回buildでPyInstallerの`contents_directory="_internal"`によりconfig、license、runtimeが`_internal`へ入ることを検出した。build scriptを、これら3つの公開assetだけportable rootへ安全に移すよう補正した。補正後はsource preflight、PyInstaller build、release-tree preflightがすべてPASSした。

## Portable smoke

build結果を次の日本語・空白を含むGit管理外pathへコピーして検証した。

`C:\xampp\htdocs\PHP\DJDmaker\build\portable-smoke\日本語 path\DJDmaker_v0.1`

実行コマンド:

```powershell
python .\packaging\verify_portable.py `
  '.\build\portable-smoke\日本語 path\DJDmaker_v0.1' `
  --report '.\build\portable-smoke\verification-report.json'
```

検証結果:

- `DJDmaker.exe`をPATHから開発環境を除いた状態で起動し、正式window titleをWin32 APIで確認した。
- `WM_CLOSE`によるsafe shutdown後、exit code 0を確認した。
- Qt `qwindows.dll`、Qt Multimedia `ffmpegmediaplugin.dll`、Playwright driver `node.exe`の同梱を確認した。
- portable `runtime\ffmpeg\ffmpeg.exe`と`ffprobe.exe`を直接実行し、双方9.0.1を確認した。
- packaged Playwrightからインストール済みGoogle Chromeをheadless persistent contextで起動し、専用profile、page content、title取得、終了を確認した。
- packaged SettingsRepositoryでJSON設定を保存し、別のEXE processを再起動して同値を読めることを確認した。
- packaged codeとportable FFmpegを使い、Fake Notebook、実MP4 fixture、RAW安全保存、Ending、HLS、ZIP、`COMPLETED`、artifact delete safety gateまでE2Eで確認した。
- source全体testは`179 passed`。
- `build/`と`dist/`のEXE、動画、ZIP、profile、検証reportが`.gitignore`対象であることを`git check-ignore`で確認した。

## 残るrelease課題

- この候補は未署名であり、正式配布物ではない。
- 約701 MBで、FFmpeg/ffprobeのfull buildが約444 MBを占める。ライセンス・codec要件を維持した軽量buildの採否は別途reviewが必要。
- PyInstaller warningにはWindowsで不要なPOSIX moduleとPlaywrightの条件付き`playwright._impl._worker`がある。実Playwright＋Chrome smokeはPASSしたが、NotebookLM live操作は別途確認対象。
- clean Windows 10/11 machine、未ログインChromeからの初回導線、コード署名、第三者license一式、配布allowlist、checksum、正式ZIPは未実施。
