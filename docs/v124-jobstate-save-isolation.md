# Ver1.2.4 JobStateSaveError job単位隔離

指示: `DJD-CHAPPY-V123-MAIN-JOBSTATE-SAVE-FAILURE-ISOLATION-CONTINUE-FULL-001`

基準main: `f35ba70815822d8f253b66da6f71e6d35f889ab2`。
phase2は `f6f5871925f44718f51a106f7f55fa44e8f882b1` のまま変更しない。
本稿は実装・検証記録。release判定は末尾のGate確定後のみ行う。

## 根因と再現

既存JsonStoreは100/200/400/800/1600/3200msの6待機、最大7 publishを行う。
枯渇後にJobRepository.saveがJobStateSaveErrorへ変換するが、旧pipelineのjob処理境界が
その例外を全体へ再throwし、GuiPipelineControllerの外側handlerがcancellationを要求、
GUI error signalからblocking QMessageBoxへ到達していた。
ファイルを一時的に保持する外部processの特定はできておらず、Dropbox原因とは断定しない。

旧経路をfixtureで再現したtraceの主要frame（基準commitの行番号）:

```text
PipelineCoordinator.run_cycle                 pipeline.py:284
  _run_cycle_lane                            pipeline.py:340
  _check_act_job                             pipeline.py:490
  _run_notebook_job / checkpoint              pipeline.py:612
  cancellation.checkpoint                    cancellation.py:80
  runtime_operation.report_operation         runtime_operation.py:67
  update -> _stage_event -> _save             pipeline.py:329,175,154
  TerminalSaveFailureJobs.save                tests/test_pipeline.py:41
JobStateSaveError: controlled terminal save failure
```

これはユーザー本番のtracebackではなく、同じ例外と全停止経路を再現した制御試験。
追加試験では実storageのos.replaceにPermissionError / WinError 5 / 32を注入し、
最大7回の実retry後の隔離も検証する。本番177 JSONへfaultを注入しない。

## 新しい境界

- atomic temp/write/flush/fsync/close/backup/replace、内容照合、復旧temporaryを維持。
- 既存retry成功なら無通知で継続。pipeline内retry待機だけCancellationTokenで中断可能。
- retry枯渇はそのjobのSaveDeferred制御へ変換する。browser/mediaの一般例外retryに
  再捕捉されないよう、既存RunCancelled同様の専用BaseExceptionをjob境界で受ける。
- 新規台本発見時、scheduler保存、TXT移動の補助保存も対象jobだけ隔離する。
- job JSONには保存待ちを書き戻さない。process共有memoryと独立journalで保持する。
- journalは `system/recovery/deferred/<job-id>.json`。job ID、台本名、日時、意図したstate、
  stage、remote成功可能性、retry数、理由、Notebook ID/URL、次の安全行動とsnapshotを保持。
  profile/cookie/tokenを保存しない。journalも失敗すればmemoryを維持して最終警告する。
- journalは壊れていても無視して再送しない。対象IDを隔離して未解決とする。
- Phase A末尾とPhase B末尾でjobごとに最大2照合試行/human Start。毎cycleのhot retryなし。
  未解決を全件生成投入済みと表示せず、安全な他jobだけPhase Bへ進める。

## 再開安全性

remote生成・予約成功の可能性がある場合はinspectのみで照合する。
GENERATING/READY/SCHEDULED_REMOTEをlocal stateへ反映し、Preset/生成/予約を再送しない。
送信後のNOT_STARTEDは「未送信」の証拠にせず、判断不能として隔離を維持する。
Notebook identityの保存前にprocessが終了したUPLOADINGも盲目的に作り直さない。

Download済みなら既存ファイルを検証し、RAWがあれば既存12項目gateで再検証する。
artifact削除済みを記録し、未確定の削除はremote状態を確認してから安全gate付きで処理する。
そのremote照合・削除はブラウザ所有スレッドで行い、FFmpeg並列workerへ渡さない。
RAWを再Downloadや上書きで置き換えない。Ending未選択は引き続きskip。

HLS検証済みdirectory・入力SHA-256・ZIPtemporaryをcheckpointへ残す。
保存失敗でこれらを破棄せず、再開時は入力hash・playlist・segment・ffprobe・codecを再検証。
ZIPtemporaryはCRC、flat entry、ZIP_STORED、HLSとの内容一致を確認してpublishする。
正式ZIPが既にある場合は検証済みpublish段階と整合する場合のみ再利用、上書き禁止。
新しいCOMPLETEDやrevisionを古いjournalで巻き戻さない。

## GUI / Stop / Close

正式stateは保存成功後のみevent発行。保存失敗は別overlay「状態保存待ち（他job続行）」を警告色表示。
並べ替え中のrow取り違えを防ぎ、選択を維持。詳細にも保留を表示する。
終了時は成功件数・保存保留件数・journal未保存件数を警告し、完全成功と扱わない。
Stop後は次job/次navigation/deferred再試行を開始しない。Window ×は既存安全shutdownを維持。

## 制御試験

`tests/test_v124_save_isolation.py`:
100件中#20固定失敗で99件継続、#20/#54/#88失敗で97件継続。
生成/予約/送信各checkpoint、Download/RAW/削除各checkpoint、実FFmpeg HLS/ZIP各checkpointの
保存失敗を注入し、重複なし・RAW保持・成果物再利用を検証する。
memory fallback、journal restart/corruption、Stop/Close、GUI overlay/sort、初期登録失敗も対象。

安全なpackage試験は既存sequential smokeを拡張し、
`DJD_PACKAGING_SMOKE=1` と `--packaging-save-isolation-smoke <fresh-root> <report>` の両方が必要。
既存directoryは拒否し、fresh fixture内のrelease0.jsonだけを拒否する。
remoteはFake、GUI/JSON/FFmpeg/RAW/HLS/ZIP/TXT移動は実物。Googleへの送信や本番書込はない。

最新版source試験 `work/v124-source-fault02.json`: PASS。
release0の21 publish拒否（7×3）中もrelease1はCOMPLETED、保存待ちoverlay表示。
journal再読込後release0もCOMPLETED、両件ともPreset送信/Download/削除各1回、Ending skip、TXT MOVED。

source Gate: `work/v124-full04.xml`、531 passed / 0 failed（既存495 + 新規36）。
compileall / diff-check PASS。Gate後に1.2.4へ更新し、version/GUI/packaging関連20件もPASS。
`work/v124-source-shutdown.json`: Stop 6.07秒 / Close 6.06秒、navigation増加0、所有process残留0。
既存GUI更新fixtureは再起動時の曖昧なUPLOADINGと区別し、通常実行中の遷移として設定した。
既存回収testのartifact期待値は実削除後のDELETEDへ訂正（削除機能は維持）。

## 継承する既知事項

- `ANNOUNCEMENT_MODAL_LIVE_AUTO_DISMISS: UNVERIFIED_LIVE`（既承認、fixture検証を維持）。
- `SOURCE_UPLOAD_ERROR_LIVE_RECOVERY: UNVERIFIED_LIVE`（原因不明の過去完了を成功証跡にしない）。
- 旧再生成可能build cleanupは既存環境ポリシー拒否を迂回しない。ユーザーデータは削除しない。

## Release Gate

2026-09-08、以下のlocal release Gateをすべて確認後にmainへの通常commit/pushを行う。
push後SHAと同期状態は最終報告で確定する。force/rebase/reset/amendは使用しない。

| Gate | 結果 / 証跡 |
|---|---|
| Ver1.2.4全suite | **531 passed / 0 failed**, `work/v124-release-full.xml` |
| Version | Python/Product 1.2.4、File 1.2.4.0、GUI Ver1.2.4 |
| Build | PyInstaller 6.22.2 onedir、既存spec、新規workpath `build/pyinstaller-v124` |
| Portable | `dist/DJDmaker_Ver1.2.4`、配置preflight PASS |
| ZIP | `dist/DJDmaker_Ver1.2.4.zip`、CRC integrity PASS、408 files |
| Package監査 | private profile/credential/runtime media 0 |
| EXE保存競合 | `work/v124-exe-save-isolation.json`: PASS、21 publish拒否、他job続行、journal復旧 |
| 重複防止 | 両fixtureのPreset送信/Download/artifact削除は各1回、両件COMPLETED/TXT MOVED |
| EXE Stop/Close | `work/v124-exe-shutdown.json`: 6.44秒/6.28秒、navigation増加0、所有process残留0 |
| EXE modal | local fixtureでdetect/dismiss/scrim消失/Chat復帰、Google liveではない |
| 標準portable回帰 | `work/v124-exe-standard.json`: GUI/設定write→別process read/browser/preset/Fake E2E PASS |
| Ending | 標準EXEはEndingあり完走、fault EXEはEndingなし完走 |
| 175jobコピー移行 | `work/v124-portable-migration-report.json`: 75 COMPLETED/100 FAILED、ID/preset/Notebook保持 |
| 原本/秘密情報 | `work/v124-final-audit.json`: 本番177 JSON変更0、tracked media/DB/検出secret 0 |
| phase2 | f6f5871925f44718f51a106f7f55fa44e8f882b1のまま、変更0 |
| compileall / diff-check | PASS |

標準portable回帰の初回並列実行はexit 1、HLS開始までのcheckpointは残ったが完了reportなし。
その実行の例外traceは取得できず、原因は断定しない。テストやEXEを変更せず、同一ZIPを
新しい日本語・空白pathへ展開して単独実行した結果、全項目PASS。
失敗した試験runtimeは成功証跡に数えず保持する。通常の本番処理で人工faultを起こしていない。

EXE: 3,922,988 bytes

```text
2CF89913DAAB07C9A63B1580B2109BE555B340F6C801B9E23B3A70CAAE11D634  DJDmaker_Ver1.2.4/DJDmaker.exe
D1B2DB938BD157321F46CCAC0909113C43BBB702F7D37ED88520B1EA8D29610B  DJDmaker_Ver1.2.4.zip
```

ZIP: 277,701,293 bytes。checksumファイルは`dist/DJDmaker_Ver1.2.4_SHA256SUMS.txt`。
旧配布物は過去のcleanup拒否を迂回せず保持。最終dist 7,820,380,746 bytes / build 66,978,914 bytes。
ユーザーデータ・元3repo・phase2・利用中の旧EXEは変更しない。
