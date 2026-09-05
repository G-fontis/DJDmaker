# DJDmaker v0.1 Final Release

指示ID: `DJD-CHAPPY-V01-UNIT5-FINAL-RELEASE-GIT-PUSH-001`

## Release Candidate

- Repository: `C:\xampp\htdocs\PHP\DJDmaker`
- Branch: `main`
- HEAD before: `f87adbbebe712cc783f471f3a2c6fbd7c5393169`
- Application: 台本から授業動画つくるマシーン v0.1
- Engines: GNBCreator / ドウガッチンガー / HLS Converter
- Credit: Created by 福ゼミ塾長
- Python package version: `0.1.0`
- Windows Product Version: `0.1`
- Windows File Version: `0.1.0.0`

## Regression

- Tests: `180 passed in 31.13s`
- Python compile: PASS
- Import/version: PASS (`0.1.0`)
- Source packaging preflight with build environment: PASS
- Real FFmpeg/ffprobe, HLS, ZIP, JSON restart, scheduler and artifact-delete fixture are included in the passing suite.

## Portable release

- Folder: `dist\DJDmaker_v0.1`
- Files: 408
- Total file bytes: 701,221,490
- EXE: `dist\DJDmaker_v0.1\DJDmaker.exe`
- EXE size: 3,755,144 bytes
- EXE SHA-256: `92C9B80F5DCCF75C0524851010105CF9A3B9D7F133854B973FEE702ADD8EE2BC`
- Package: `dist\DJDmaker_v0.1.zip`
- Package size: 271,836,206 bytes
- Package SHA-256: `EF97C3202D09CE5ADB5F61146B50CD4B853189CF1D484AE29F30E0B8A72A1022`
- Checksums: `dist\SHA256SUMS.txt`

The package contains one top-level `DJDmaker_v0.1` directory, 423 ZIP entries, and one `DJDmaker.exe`. `ZipFile.testzip()` returned `None`.

## Fresh expansion verification

The pristine portable folder was copied to a newly-created Japanese/space path and verified. The formal ZIP was then extracted into another newly-created Japanese/space path and independently verified. Both runs passed:

- GUI launch, exact title and safe shutdown
- default settings creation and cross-process restart/readback
- writable runtime folder creation
- packaged Playwright and installed Chrome launch
- bundled FFmpeg and ffprobe 9.0.1
- Fake Notebook → RAW → Ending → HLS → ZIP → COMPLETED
- artifact deletion safety gate

The formal `dist\DJDmaker_v0.1` tree remained pristine: input, raw_files, output, work, logs, browser and system contained no runtime files when packaged.

## Safety and Git

- No tracked DB, SQLite file, browser profile, Cookie, session, token, RAW/edited MP4, TS, M3U8, generated ZIP, log, temporary download or `.crdownload`.
- High-confidence credential signature scan returned zero matches in production files.
- `.gitignore` retains runtime, secret, media, build and dist exclusions.
- AutoGeminiNoteBookCreator, GeminiNotebookVideoMerge and FukuzemiApp source repositories remain unchanged and clean.
- Release commit message: `release: DJDmaker v0.1`
- Push policy: normal `main` push only; no force push or history rewrite.

The binary is not code-signed. Users should verify the published SHA-256 before running it.
