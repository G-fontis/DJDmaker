# Ver2.3 Download完了・Deferred Queue release記録

指示ID: `DJD-CHAPPY-V23-DOWNLOAD-COMPLETION-DEFERRED-QUEUE-FULL-002`

開始HEADは`357e5553f6ae1ff8c2c92f110d1ed44c1f09a400`（main、Ver2.2）。既存の未commit V23差分を保持して統合し、reset/revert/amendは行っていない。

## Root causeとDownload transport

人間の標準Downloadは同一artifactを2/2回、43,865,591 bytes、同一SHA-256で保存できた。一方、旧Playwright `remote-debugging-pipe` 起動ではChrome 153/154がnative download popup生成後にCrashpadへ落ち、2～3MBで転送が停止した。Crash dump時刻と各失敗時刻は一致した。Google応答・artifact・HTTP 200は正常で、Google側生成失敗ではない。

productionは通常の専用profile Chromeをloopback CDP port付きで起動し、Playwrightを`connect_over_cdp`でattachする方式へ変更した。Notebookの「その他」→「ダウンロード」は通常locator clickを使い、synthetic JavaScript clickは使わない。Notebook自身のpopupイベントとPlaywright Download objectを捕捉し、`save_as`完了後に`failure()`、size stable、ffprobe/video/duration、正式3σ品質Gateを確認してatomic publishする。

旧Download専用`about:blank` keeperは撤去した。一般browser初期化が既存blank pageを再利用する処理は維持するが、Download開始時に予備blank tabは作らない。「無題」popupはNotebook/Chromeの正規経路であり、それ自体を成功判定には使わない。

## GuardとDeferred Queue

共通`DownloadTransferGuard`がactive countと`RUNNING_DOWNLOAD`を管理し、同時transferを直列化する。page/popup/context/browser close、automation/pipeline/error cleanup、navigation、次Notebook、次job、phase transition、Stop、App Closeはactive中にError化せずpersistent Deferred Task Queueへ登録する。queueはtask/job/target/command identityでdedupし、USER_STOP、APP_CLOSE、retry/recovery、navigation/job、scheduler、cleanupの順で解放する。runtime-only close taskはrestart時に破棄し、manual retry、recovery、next job、unrecovered scanは復元する。

active countが0になった後だけrepository reload、capability refresh、user command、candidate discovery、priority再計算を行う。active transfer中はscheduler waitおよび`ALL_TASKS_COMPLETED`を禁止する。Stopは`STOP_REQUESTED_WAITING_DOWNLOAD`、App Closeは`APP_CLOSE_WAITING_DOWNLOAD`として完了後に実行する。close要求・実行はreason/caller/job/time付きで記録する。

`DOWNLOAD_VERIFY_FAILED`はrecoverable、6 attempts枯渇後の`DOWNLOAD_RETRY_EXHAUSTED`はmanual retry可能な`RECOVERABLE_EXHAUSTED`/`ATTENTION_REQUIRED`として扱う。保存リトライと未回収チェックは既存Notebookだけを再確認し、Notebook create、source upload、Preset send、generation request、Studio Retry/Deleteは行わない。

## Testsとlive acceptance

- baseline: 909 tests。最終: `914 passed in 328.38s`。compileall、`git diff --check`（改行warningのみ）PASS。
- 同一artifact自動Download: 3/3 PASS。各43,865,591 bytes、SHA-256 `ECB70A6BB37162885D0E8AD74BBBF0AD62734D4A6240E93E8A9FD9E97220F05E`。2～3MB途中停止0、3σ PASS。
- Live Queue: Download中NEXT_JOBを保留し、完了後release PASS。Live Stop、Live App Closeもtransferを中断せず完了後release PASS。
- 11job recovery copy: remote READY 11/11を既存Notebookから回収し、11/11 `COMPLETED`、errors 0。全件1 attempt、artifact `RETAINED`。Notebook create/source upload/Preset send/generation/deleteは各0。
- 未回収scan copy: pending 1、checked 1。remote READYを再確認し、attempt 6のため`DOWNLOAD_RETRY_EXHAUSTED`/`RECOVERABLE_EXHAUSTED`として安全保持。正常完了へ誤分類しない。
- 本番job JSONおよびbackup: 22ファイルの前後SHA-256一致、変更0。remote artifact削除0。

## Buildとpackage

- Version: Python/Product `2.3.0`、FileVersion `2.3.0.0`、GUI `Ver2.3`。
- PyInstaller 6.22.2 onedir、Python 3.14.6、PySide6 6.11.2、FFmpeg/ffprobe 9.0.1。
- Portable: `dist/DJDmaker_Ver2.3`。実EXE GUI、settings save/restart、browser smoke、preset、Fake E2E、FFmpeg/ffprobe PASS。
- Fresh ZIP展開: 日本語＋空白pathから同じportable検証を全てPASS。
- EXE: 4,130,405 bytes、SHA-256 `42D2620190CBDB27963F0298186361D6C35AA831099B8C3D15922510923FD134`。
- ZIP: 272,214,802 bytes、SHA-256 `11B2F6CEFC8BA173E6116D1C2E99D667A78E682E22C79A8B3241D1F47EAF77A8`。
- 正式folderのinput/raw/output/work/system/logs/browserは全て0ファイルで、browser profile、Cookie/Login Data、job state、実RAW/MP4/HLS/ZIP、`.crdownload`、fixture、debug logを含めない。

PowerShell `Remove-Item`による候補内Fake成果物cleanupは環境ポリシーが実行前拒否したため、対象rootを厳密検証した上で過去に承認・成功済みの通常.NET削除APIを使った。削除対象はVer2.3 build候補内の検証器生成物だけで、ユーザーデータ削除は0。
