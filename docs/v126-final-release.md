# Ver1.2.6 Release Gate

指示ID: `DJD-CHAPPY-V126-CURRENT-WINDOW-DUALGUI-PRESET-WORK-STEALING-SCHEDULER-FULL-001`

返答ID: `DJD-CODEX-20260908-V126-FINAL-001`

Repository: `C:\xampp\htdocs\PHP\DJDmaker` / main

本記録はcommit直前の製品Gate記録。全製品GateはPASS。commit自身のSHA・push後照合を含む最終56項目報告は `work/v126-final-release-result.txt` に保存する。過去の試行と根因調査は[経過記録](v126-work-stealing-source-gate.md)を参照。

初回候補EXEの上限中回収・603.58秒待機はPASS。一方、保存隔離verifierが旧自動終了を待つためtimeoutした。新仕様どおり待機した製品動作を変更せず、verifierが待機確認後に明示Stopし、再Startで復旧を確認する方式へ訂正。sourceと最終EXEの再試験は21回の保存失敗注入、他job継続、overlay、重複なしの復旧でPASS。初回候補は `work/v126-build-candidate-001/` に履歴として保持。下記は再build後の最終値。初回からのSHA変更理由は検証機構の訂正であり、生成・回収・Scheduler本体は初回候補と同一。

## 確定仕様

生成の停止トリガーは、実上限/Chat無効の明示表示、または今回の生成依頼への新しい返信が示す必要クォータ不足。almost警告、回答中の一時disabled、過去返信だけでは停止しない。動画完成確認・Downloadの可否はクォータと独立する。

生成→Local→期限到達artifact回収→Error再診断→600秒割込み可能待機の順。未完了jobが残れば終了しない。Errorは永続最大3回、terminalを成功とは数えない。Ending任意・RAW不変・保存失敗隔離・Stop/Closeを維持。

## 成果物

- Portable: `dist/DJDmaker_Ver1.2.6/`
- ZIP: `dist/DJDmaker_Ver1.2.6.zip`
- checksum: `dist/DJDmaker_Ver1.2.6_SHA256SUMS.txt`
- EXE: 3,991,343 bytes / SHA-256 `6786D33B09690EDE277BB6C8D4190C53BBD966E36575FC1372849236F5442469`
- ZIP: 277,780,803 bytes / SHA-256 `636087913E6DEB899B1A91E44089F2A09F7E4F08DB1E12F812FE06E82EA09339`
- Python/Product 1.2.6、FileVersion 1.2.6.0、GUI Ver1.2.6。
- 正式PyInstaller 6.22.2 onedir/windowed、Python 3.14.6、PySide6 6.11.2、FFmpeg/ffprobe 9.0.1。

## 56項目

| # | 項目 | 結果 |
|---|---|---|
| 1 | HEAD before | f2dced0cb0dd88017dcccf7978f6a128677223eb |
| 2 | Chrome history旧ロジック | 検索したが履歴による現在状態推測分岐は未発見。原因を捏造しない |
| 3 | current-state | 既存process.poll/context接続/profile lockを維持、契約テストで保護 |
| 4 | legacy削除 | 該当分岐未発見のため削除0。診断/Chat履歴は別用途として維持 |
| 5 | Phase1再開button | 0。Pause解除はStart、内部resumeは保持 |
| 6 | Phase2再開button | 同上 |
| 7 | Phase2 Preset root cause | 切替時の共通読込・runtime再適用の欠落を修正。元環境の全因果関係は未確定 |
| 8 | shared Preset | 同一Repository/既存CRUD service/ViewModel/JSON。同一session選択共有、起動時未選択 |
| 9 | Scheduler旧停止原因 | 上限中local後returnとcontroller終了条件 |
| 10 | architecture | 共通backendのCapability/task discovery、永続job/checkpointを使用 |
| 11 | Priority 1 | 生成可能なら最高優先。almost中のREV024/REV115生成開始live確認 |
| 12 | Priority 2 | 上限中もRAW/Ending任意/HLS/ZIP/保存復旧 |
| 13 | Priority 3 | local終了後に期限到達artifact確認。完成したREV115を同一jobで回収 |
| 14 | Priority 4 | Errorのremote/checkpoint診断、最大3回の永続retry、他job継続 |
| 15 | Priority 5 | 未完了・実行可能0なら600秒wait後再探索 |
| 16 | Capability | generation/upload/chatとartifact/download/localを分離 |
| 17 | Limit中Generation | live不足返信検出後Preset追加0、新規予約0 |
| 18 | Limit中Local | 実回収REV115→HLS/ZIP完了1件、独立RAWコピー再処理1件も完了 |
| 19 | Limit中Artifact check | batch第2cycleでREV024とREV115を確認、その後REV024を期限後再確認 |
| 20 | Limit中Download | live1件（REV115）。12RAW gate全PASS |
| 21 | local終了後 | 実GUIでlocal→REV024確認→WAITを確認。local完了だけでは終了しない |
| 22 | 10分Wait | source実GUI600秒後の第2cycleを確認。最終EXEで603.844秒後に再探索PASS |
| 23 | Limit解除後Generation | +5分/positive再確認/優先順位復帰をtests確認。今回の実返信約3時間後を意図的に早めない |
| 24 | Error retry | 上位task終了後、job単位で再評価 |
| 25 | checkpoint resume | 既存Error REV121のREADYをcopyでDOWNLOAD_PENDINGに復旧。source不良REV018は送信せず保持 |
| 26 | Unknown fallback | 同一Notebookのsource状態から再確認。正常source重複投入なし |
| 27 | retry 3回 | coordinator再作成後も回数保持するcontract PASS |
| 28 | terminal failed | 当該jobだけterminal。他job継続、COMPLETEDと区別 |
| 29 | auto stop | 全job completed/terminalかつ保存保留なし。未来待機は停止しない。tests PASS |
| 30 | Pause | source/最終EXEでwait割込み・Start再開・Stop PASS。上限中生成呼出0 |
| 31 | Stop | source実GUIで待機境界Stop後の追加Notebook/送信/Downloadなし。最終EXE shutdown PASS |
| 32 | Job save isolation | 最終EXE PASS。保存失敗21回注入、他job完了、overlay、明示Stop後復旧、重複0 |
| 33 | Phase1 GUI | source組立・切替・Preset PASS、展開EXE起動PASS |
| 34 | Phase2 GUI | source/EXE画像目視。Ver1.2.6、Preset表示、再開buttonなし |
| 35 | Runtime | 優先度/Capability/task/retry/次回確認/terminal表示。quota返信検出時にalmostの「生成継続」が残らないよう明示更新 |
| 36 | tests | 最終607 passed / 358.21秒。work/v126-release-final-tests-003.xml。compileall PASS |
| 37 | Limit live | REV105の現在返信「動画生成に必要な利用枠不足」。ユーザー追加承認の正式トリガー。Chat無効bannerは未観測 |
| 38 | Download live | REV115 RAW 2,127,323 bytes、356.821043秒、HLS/ZIP/TXT移動COMPLETED。artifactはDELETE_PENDINGで保持 |
| 39 | Wait live | batch第1wait後600秒で再探索、未完成は再待機・完成は回収。試験が次waitで協調Stop |
| 40 | Error live | REV018/REV121の既存remote判定をcopyで確認。送信・削除なし |
| 41 | 本番data変更 | Ver1.2.4/1.2.5 system JSON 1,030件再照合、変更0。元3repo clean、phase2参照不変 |
| 42 | Version | 1.2.6 / 1.2.6.0 |
| 43 | cleanup | 旧dist Ver1.2.5 portable/ZIPの2対象409ファイル削除。979,211,402 bytes削減、残容量0、失敗0、保護変更0 |
| 44 | portable | build exit0、ZIP CRC PASS、展開全408配布file hash一致 |
| 45 | EXE Scheduler | 最終EXEでlocal・回収ともCOMPLETED、100件WAITING保持、603.844秒後再探索PASS。cloud calls 0、check/download各1 |
| 46 | EXE dual GUI | 両mode表示・切替、3つの独立processでPHASE2→PHASE1の選択復元PASS |
| 47 | EXE Preset | 最終展開EXEで2件永続・再起動選択空PASS。dual verifierで選択共有・両GUI再開button0 PASS |
| 48 | EXE Pause | Pause→Start→Stop PASS |
| 49 | package audit | profile/credentials/runtime media混入0、必要Python base_library.zipのみ許可。正式distへ試験runtimeを書き戻さない |
| 50 | commit | 全製品Gate PASS、mainの本release差分のみ対象。実SHAは最終外部報告へ記録 |
| 51 | push | main通常pushのみ。実結果は最終外部報告へ記録 |
| 52 | HEAD/origin after | 最終push後にwork/v126-final-release-result.txtへ記録 |
| 53 | ahead/behind | commit前fetch時0/0。push後結果は最終外部報告へ記録 |
| 54 | Git status | commit前差分は本releaseのsource/test/docs/metadataのみ。push後結果は最終外部報告へ記録 |
| 55 | known issues | 下記参照 |
| 56 | 未解決 | 製品の未解決重大事項0。許容済みlive未確認・remote生成待ちは下記。Git最終照合は外部報告へ記録 |

## 試験境界・既知事項

- 本番ジョブ原本は変更しない。試験Notebookと元jobの対応は各copy jobのparent_job_idに保持。本番側のWAITINGを再開する前に対応を照合し、重複生成しないこと。
- REV024の最終copy stateは `work/v126-final-collection-002/system/jobs/04e4c7bd71854e01b944b524a8b1db69.json`。生成中のため再送しない。REV115の完成RAW/ZIPは `work/v126-batch-live-001/`。回収後もremote artifact保持。
- REV105の返信「約3時間後」から16:09:38 JST（＋5分16:14:38）を算出。これは返信由来の概算で、almost bannerの16:21という時刻と同一ではない。現在返信による不足と、Chat全体無効化は区別する。
- `ANNOUNCEMENT_MODAL_LIVE_AUTO_DISMISS: UNVERIFIED_LIVE`、`SOURCE_UPLOAD_ERROR_LIVE_RECOVERY: UNVERIFIED_LIVE`は従来のユーザー許容事項。fixture保護を維持し、強制再現しない。
- TAX087の「作成中」返信とartifact未生成の不一致原因は未確定。他Notebookの同タイトル生成物が原因だと断定しない。
- 初回版数変更時の残存表記を検知してbuild/testを停止・訂正・再実行した。最終607件は訂正後。実行ポリシー変更・迂回なし。
- Cleanupは検証済みの旧正式distだけを対象とし、PowerShellの通常削除で成功。元1.2.5はGitのf2dced0から再生成可能。旧SHA/docs/Git履歴、初回1.2.6候補の検証履歴、本番データ、profile、今回live RAW/ZIPは保持した。ユーザーのDropbox運用フォルダには配布物を上書きしていない。
- 最終証跡: `work/v126-final-exe-limit-report.json`、`work/v126-final-exe-isolation-report.json`、`work/v126-final-exe-shutdown-report.json`、`work/v126-final-exe-gui-{0,1,2}.json`、`work/v126-final-package-report.json`、`work/v126-final-extracted-portable-report.json`、`work/v126-cleanup-report.json`。

製品Gate判定: PASS。最終release判定は通常push後のHEAD/origin/GitHub main一致・0/0・Git cleanを確認して `work/v126-final-release-result.txt` に記録する。
