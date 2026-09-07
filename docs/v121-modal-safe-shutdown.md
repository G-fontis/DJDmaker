# Ver1.2.1 modal / safe shutdown / Ending任意化 検証報告

## 最新検証結果（2026-09-07）

返答ID: DJD-CODEX-20260907-V121-RELEASE-001

指示ID: DJD-CHAPPY-V12-GEMINI-MODAL-SAFE-SHUTDOWN-HOTFIX-MERGED-FULL-001

ユーザー追加指示を適用: modal live自動dismiss未確認を既知事項として許容し、強制再現しない。Ending選択を動作必須条件にしない。

**source / package検証PASS**。commit/pushの実行結果・確定SHAは最終応答に記録する。以下はcommit前に確定した検証結果であり、後段の初回BLOCKED報告を更新する。

1. HEAD before: `297b64c2e98d542aade471ae4befc262ca79834a`。
2. Stop root cause: stop eventがpipeline内部のresume/待機/mediaに伝わらず、100件controlled試験でStop後99件の診断継続を再現。
3. Shutdown root cause: 継続workerのjoin期限切れ。別threadからのPlaywright cleanupを防ぐ所有確認も不足。
4. Worker: Qt GUI / QRunnable command / Python pipeline thread / media executor。unsafe thread killや大規模process化なし。
5. Cancel: run単位のCancellationToken/Eventを一本化、ContextVarとcopy_contextでmediaまで伝播。
6. Wait: 100ms browser poll、観測waitは最大2秒slice＋元deadline。actionの元timeoutを維持し、副作用actionを再実行しない。
7. Queue: dequeue/dispatch前のcheckpointで停止後の新規job開始を防止。
8. Navigation: goto前後check、Stop後の追加navigation 0。
9. Retry: interruptible sleep / checkpointで停止後retryを抑止。
10. Browser shutdown: 所有workerでcontext/Playwright cleanup。6秒watchdogで固着時のOS fallback。
11. Ownership: Chromeを起動する前に当該Playwright driverだけを匿名Windows Job Objectへ登録。通常ユーザーChromeやAUTH ChromeをこのJobへ含めない。
12. FFmpeg: Ending/HLS両adapterで実FFmpegの待機中cancelを試験。terminate→bounded wait→必要時kill、子process残存0。
13. Download: 一時download/RAWコピー/publish前checkpoint。停止時に一時物を正式RAWとして採用しない。
14. Checkpoint: `STOPPED:<state>`保存、UPLOADINGはRECOVERY_PENDING。StopをFAILED扱いしない。
15. Restart: 同一job/Notebook/preset snapshot保持のfixture、GUI Stop→再Start、media stage別resume試験PASS。EndingなしRAW_READY/ENDING/HLS_ENCODING/ZIPPINGの再開もPASS。
16. closeEvent: Stopと同じshutdown、二重close guard、終了中操作無効化。
17. Timeout: cooperative join6秒→所有process fallback→join4秒。現在operation/job/所有driver情報を診断へ記録。
18. Fallback: 名前検索・全Chrome killではなく保持Job handleだけをterminate。実Chromeの応答しないevaluateを使い、controller Stopでworker終了・error callback0を確認。無関係process保持もPASS。
19. Popup: source実GUI・packaged Stop/Closeともerrors0。
20. Child leak: Windows Job accountingでcleanup後active process0。実FFmpeg childも終了。
21. Thread leak: packaged Stop/Closeでworker.is_alive() false、EXE exit code0。
22. Live Stop: 175件コピー（FAILED100件）、navigation 2→2、約1.9009秒、error0。
23. Live Window X: navigation 2→2、約2.3747秒、window hidden、error0。実ページのread-only source確認/返信待機/artifact poll中cancelは各0.1秒未満。新規送信・uploadなしのcontrolled観測である。
24. Modal DOM: 指定Notebook/reload/別Notebookで各12秒観測、visible dialog0。**LIVE_AUTO_DISMISS: UNVERIFIED_LIVE（ユーザー承認済み）**。
25. Classifier: ANNOUNCEMENT/ONBOARDING/SAFE_INFO/ACTION_CONFIRMATION/UNKNOWN。未知・削除/購入等の確認dialogを自動closeしないfixture PASS。
26. Dismiss: dialog内の確定closeだけを1回操作。productionでは旧global optional-closeを使わず、新guardを使用。source pickerのXを情報dialogとして閉じない。
27. Scrim: fixtureのdialog非表示＋scrim消失を確認。packageも2回PASS。
28. Chat: fixture dismiss後trial/click復帰、実Notebookのmodal非表示状態でfocus成功。live dismiss後復帰そのものは未確認。
29. Source: guardと実ページのsource ready確認中Stopを検証。live modal後のSource復帰は上記既知事項の範囲で未確認。
30. Studio: modal fixtureにより遮断解除を検証。live modal後のStudio復帰は未確認。
31. Modal中Stop: 中断後追加dismiss0、未知dialog安全停止fixture PASS。
32. --no-sandbox: Playwright既定値由来。今回flag/認証方式/専用profile運用を変更していない。OS ownership境界を追加したが通常Google認証の起動commandは変更0。
33. Generate later: 予約実装・既存fixtureを維持。現行通常ページのread-only smokeではbutton非表示。新規予約を強制発行していないため実予約成立の再証明ではない。
34. Quota: 現行表示から取得できずCREDIT_UNKNOWN、percent/reset null。誤って枯渇・5時間後resetと決めつけない。既存quota/reservation回帰tests PASS。クレジットを意図的に消費して枯渇再現しない。
35. Completed Generate: 今回のlive確認で新規生成0、COMPLETED再生成0。
36. Source reupload: 今回のlive確認でupload0。
37. Tests: **全401 passed / 242.94秒**（Version 1.2.1整合を含む）。その後、Source/Studio操作復帰fixtureを2件追加し、対象file **35 passed / 27.68秒**。計403個の異なるtestがPASS（403件一括実行の記録ではない）。製品sourceの追加変更なし。compileall / git diff --check PASS。
38. 175件copy: コピーだけをGUI試験に使用し、pathを隔離先へ変更。
39. 本番JSON: job175＋settings/presets2をSHA-256で再照合、177件すべて一致・変更0。
40. Portable: `dist/DJDmaker_Ver1.2.1/`。既存PyInstaller onedir specでbuild。PowerShell .ps1起動は実行制限で拒否されたため、policyを変えず同じspecをPyInstallerへ直接指定し、同じasset配置とpreflightを実行。
41. EXE Stop: **4.4218秒**、worker終了、所有process0。
42. EXE Close: **1.7950秒**、worker終了、所有process0。通常GUI終了もexit0。
43. EXE leak: shutdown smokeの両runでOS accounting active process0。検証後の対象EXE/Chrome/FFmpeg残存0。
44. ZIP: `dist/DJDmaker_Ver1.2.1.zip`、277,645,184 bytes、ZIP integrity PASS。日本語＋空白pathへ展開し、GUI/settings再起動/browser/preset/Fake E2E/Ending/HLS/ZIP/Stop/再Start/Close/modal fixtureを確認。配布rootのruntime/profile/credentials/media混入0。
45. SHA-256: EXE（3,866,555 bytes）`ADFDB37C8DE7F0B802A058A84D6D4503C40A08ECCA204E70992AA499E8459942`。ZIP `7C7FAB8F449CFB175F4AD25A4730DC29B1485FAF09482ECD5EB7436B5FDCB7AB`。`dist/DJDmaker_Ver1.2.1_SHA256SUMS.txt`にも保存。
46. Version: Python/Product1.2.1、File1.2.1.0、GUI Ver1.2.1。Ending未選択でもEXEのStart enabledをaccessibility treeで確認。Endingなし実FFmpeg E2EはCOMPLETED、RAW SHA不変。選択時の既存Ending付きpackage E2EもPASS。
47. Cleanup: 旧Ver1.2のruntimeファイル0、旧EXE SHAとsource commitを確認して削除を試みたが環境policyによりcommand実行前に拒否。削除0。再生成可能物でありユーザーデータではない。旧配布物は保持し、別手段で突破していない。旧Ver1.1/phase2配布物も触っていない。現在dist **3,904,108,221 bytes**、build **13,310,884 bytes**（PyInstaller中間物は今回のもの）。
48. phase2: ref `46c4599e76383b663552aec3f052e0dca8cc09f9`のまま。phase2-v12/phase3作成0、元3repoへの変更0。
49. Commit: 全検証後、この文書を含む今回変更だけをmainへ通常commitする。確定SHAは最終応答で報告。
50. Push: `origin https://github.com/G-fontis/DJDmaker.git`へmain通常pushのみ。force/rebase/reset/amendなし。実行結果は最終応答で報告。
51. HEAD/origin: push前baseはいずれも297b64c。push後はfetchと実remote refで一致を検証する。
52. Ahead/behind: push後0/0およびGit cleanを検証して最終応答に記録する。
53. 既知事項: live modal自動dismissは未確認（承認済み）。`SOURCE_UPLOAD_ERROR_LIVE_RECOVERY: UNVERIFIED_LIVE`を継続。現行通常UIで予約buttonが出なかったため予約action再発行なし。非表示起動の追加検証ではMainWindowHandle取得方法が不適切だったが、実HWNDでEnding未選択/Start enabledとWM_CLOSE終了を確認して解消。製品のshutdown failureとは区別する。

## 初回途中報告（以下は履歴・上の最新結果を優先）

返答ID: DJD-CODEX-20260907-V121-HOTFIX-PROGRESS-001

指示ID: DJD-CHAPPY-V12-GEMINI-MODAL-SAFE-SHUTDOWN-HOTFIX-MERGED-FULL-001

対象: `C:\xampp\htdocs\PHP\DJDmaker` / `main`

## 判定と証拠の範囲

2026-09-07のユーザー補正: **modal live自動dismissは未確認の既知事項として許容**。fixtureを維持して残工程へ進む。強制再現は禁止。以下の過去の途中報告は証跡として保持するが、modal未確認のみをrelease blockerにはしない。他の停止・終了・package Gateは免除されていない。

**BLOCKED / 未完了**。sourceの協調停止実装と限定的な実GUI Stop / Closeは確認したが、現行告知modalの実表示を検出できていない。fixture成功をlive成功へ読み替えない。以下の未完了事項も残るため、Version変更、build、commit、pushは実施していない。

本番の175 job JSONとsettings/presetsを、既存の移行前backup（`work/v12-migration-final-42d58yq_/system/migration-backups/resume-v2`）とSHA-256照合。177ファイルすべて一致、差分0。GUI試験はコピーだけを使い、入出力pathも隔離先へ変更した。

## 53項目の途中報告

1. **HEAD before**: `297b64c2e98d542aade471ae4befc262ca79834a`。
2. **Stop root cause**: GUI controllerのstop eventがpipeline内のresume診断ループ、同期Playwright待機、media処理へ伝播していなかった。100件のcontrolled再現ではStop後も残り99件へ進んだ。現行sourceでは同試験の追加navigation 0。
3. **Shutdown root cause**: 継続中workerへのjoinが期限切れとなり、`shutdown: pipeline worker did not stop safely`。controlled再現で確認。以前のGUI側cleanupにはPlaywright所有threadを保証するチェックもなかった。
4. **Worker architecture**: GUIはQt、操作commandはQRunnable、pipelineはPython thread、mediaはThreadPoolExecutor、browserはsync Playwright。process化はしていない。
5. **Cancellation**: 一つのCancellationToken/EventをContextVarでrunへ渡す。media executorへcopy_contextで伝播。RunCancelledは通常のjob failure/retryと区別する。
6. **Wait**: Event待機、browser poll100ms、観測waitを最大2秒slice＋元deadlineのouter loopに変更。click等の副作用actionは自動再実行しない。launch/closeなどの長時間blockingに対する完全な上限保証は未完了。
7. **Queue**: resume/recovery/notebook/media dispatch前checkpoint追加。
8. **Navigation**: goto前後checkpointとcounter。Stop後に新規goto/page生成を開始しないfixtureを確認。
9. **Retry**: retry sleepをinterruptible化。各待機phaseの共通token試験あり。ただしすべての実adapter経路でのlive Stopを完了したわけではない。
10. **Browser shutdown**: 正常経路ではpipeline worker自身のfinallyでcleanup。今回のStop/Close実測でcontext終了。
11. **Ownership**: automation作成thread IDを保持し、別threadからのPlaywright cleanupを拒否。通常ユーザーChromeを一括終了しない。
12. **FFmpeg**: Popen handle保持、communicateを短時間poll、cancel時terminate→2秒wait→kill→2秒wait。実子processを使うfixtureで終了を確認。実FFmpeg長時間encode中のlive Stopは未完了。
13. **Download**: browser待機にtoken、RAW copy/publishにcheckpoint。一時downloadを停止時に正式RAWとして採用しない。全download方式のlive中断は未完了。
14. **Checkpoint**: `STOPPED:<state>`を保存。UPLOADING中断はRECOVERY_PENDING。StopをFAILEDへ変換しない。
15. **Restart**: fixtureで同一job ID/Notebook ID/preset snapshotの維持と再開を確認。すべての実remote/media stageでのrestartは未完了。
16. **closeEvent**: Stopと同じshutdown経路、二重close guard、終了中GUI無効化。実GUI ×で非表示・エラー0。
17. **Timeout**: worker join10秒、recovery待機10秒。診断にoperation/job/child PID/navigationを含める。ただしbrowser launch/closeの完全なbounded化と所有情報を含むtimeout診断の拡充は未完了。
18. **Fallback**: media subprocessのbounded terminate/killは実装済み。browser hang時の所有process限定fallbackは未完了。Python threadをunsafe killしていない。
19. **Popup**: 今回のsource実GUI Stop/Closeともエラー通知0。timeout例外経路は残るため全条件PASSとはしない。
20. **Child leak**: subprocess fixtureで子process終了。実GUI試験後、隔離profileを使うChrome process検索は0件。package全子process監査は未実施。
21. **Thread leak**: source試験programは正常終了。全thread状態の明示assert/package検証は未完了。
22. **Live Stop**: 175件コピー（FAILED100件）のqueueを使用。navigation **2→2、追加0**、Stopから終了検出 **1.857秒**。証拠 `work/v121-stop-live-i4l0a3rd/report.json`。
23. **Live Window X**: navigation **2→2、追加0**、終了検出 **2.412秒**、window hidden、errors空。証拠 `work/v121-stop-live-daxigi0v/report.json`。reply/source/artifact各待機中の個別live試験は未完了。
24. **Modal DOM**: 現行既存Notebookを開いたがvisible dialog **0**。`work/v121-live-modal-report.json` の `live_modal_observed: false`。実DOM auto-dismissは未確認。
25. **Classifier**: ANNOUNCEMENT/ONBOARDING/SAFE_INFO/ACTION_CONFIRMATION/UNKNOWN。確認/削除/購入系は閉じない。未知dialogは安全停止。
26. **Dismiss**: dialog内部のsemantic closeだけを一回操作するfixture PASS。旧optional-dialog処理との統合、および正規upload dialogを妨げないことの追加点検が必要。
27. **Scrim**: dialog非表示＋既知backdrop消失を待つfixture PASS。実サイトのbackdrop適合は未確認。
28. **Chat復帰**: dismiss後target trial clickのfixture PASS。live未確認。
29. **Source復帰**: 対象controlのtrialを実装。正規upload dialogを含むlive未確認。
30. **Studio復帰**: live未確認。
31. **Modal中Stop**: 待機途中cancelで追加close操作0のfixture PASS。
32. **--no-sandbox**: DJDmakerは直接追加していない。installed Playwright `driver/package/lib/coreBundle.js`には `options.chromiumSandbox !== true`時の既定flag追加がある。今回この設定を変更していない。sandbox有効化のFresh auth検証は未実施。
33. **Generate later**: 既存予約実装は削除していない。新UI実予約smoke未実施。
34. **Quota**: 既存回帰tests対象。5時間refresh表示を使う新たなlive smoke未実施。
35. **Completed Generate**: 今回の隔離live試験では新規Generateを行っていない。COMPLETED再生成0。
36. **Source reupload**: 今回の隔離live試験は既存Notebook診断開始でStop。upload実行0。
37. **Tests**: 中間全385 PASS。追加修正後の全suite結果は下の最終再確認欄へ記載。compileall / git diff --check PASS。
38. **175件copy**: live Stop/Close双方で175件保持。元source/input/outputへ書かない隔離設定。
39. **本番JSON**: 177ファイルSHA-256一致、差分0（job175＋settings/presets2）。試験scriptの固定値ではなく別途hash照合で確認。
40. **Portable**: source/live Gate未完了につき新buildなし。
41. **EXE Stop**: 未実施。
42. **EXE Close**: 未実施。
43. **EXE leak**: 未実施。
44. **ZIP**: 新packageなし。
45. **SHA**: Ver1.2.1成果物なし、SHA未確定。
46. **Version**: 正式版の1.2.0 / GUI Ver1.2を維持。1.2.1へ変更していない。
47. **Cleanup**: 今回新build/削除なし。旧配布物の前回policy拒否を迂回しない。ユーザーデータ保持。
48. **phase2**: 変更0、ref `46c4599e76383b663552aec3f052e0dca8cc09f9`。phase2-v12/phase3作成なし。
49. **Commit**: 未実施。hotfixは未commitの作業差分。
50. **Push**: 未実施。force/rebase/reset/amendなし。
51. **HEAD/origin**: ローカルHEADと取得済みorigin/mainとも `297b64c2e98d542aade471ae4befc262ca79834a`。このhotfix中にpush/fetchはしていない。
52. **Ahead/behind**: 取得済みtracking ref比較0/0。ただしworking treeは変更あり、cleanではない。
53. **未解決**: 現行modal実表示とauto-dismiss、browser timeout fallback/long-call上限、旧dialog処理の統合、各実待機phase Stop、全経路restart、Generate later/quota live、packageおよびprocess/thread leak Gate。全PASSまでrelease禁止。

## 最終再確認

### 継続作業（modal例外承認後・Ending任意化追加指示）

- modal未確認はユーザー承認済みの既知事項として保持。強制再現なし。
- Windows匿名Job Objectへ、Chrome起動前の当該Playwright driverを登録。6秒の協調停止期限後は当該Job Objectだけを終了する。名前検索によるChrome一括killやPython thread killは使用しない。Playwright object cleanupは所有workerで行う。closeにも6秒watchdogを設け、所有process数が0になったことを確認する。
- 実Chromeの応答しないevaluateでfallbackを検証。GuiPipelineController Stop後、worker終了・エラーcallback0を確認。無関係なprocessを終了しないOS ownership試験もPASS。
- 最新175件コピーlive Stop: 1.9009秒、navigation 2→2。Window ×: 2.3747秒、navigation 2→2。両方errors0。Stop前の試験開始待ちが長かった原因は未特定であり、Stop応答時間と混同しない。
- 実ページのread-only probe: source ready確認中0.0114秒、未送信の新規返信を待つcontrolled観測0.0858秒、artifact poll中0.0006秒でcancel。いずれも追加navigation0、送信0、upload0。生成中の自然な返信遅延を作った試験ではない。
- source GUI smoke: Stop→再Start→Window ×、ローカルmodal fixtureのdismiss/scrim消失を2回確認。Stop 3.7041秒、Close 1.9725秒、両方worker終了・所有process0・errors0。証跡 `work/v121-source-shutdown-report3.json`。
- Ending未選択をStart/回収の必須条件から除去。未選択はNoneで保持し、検証済みRAWからHLS/ZIPへ進む。Ending結果は `SKIPPED (not configured)`。指定済みファイルが消失した場合は未選択と混同せずエラーにする。
- Endingなし実FFmpeg E2E: COMPLETED、HLS/ZIP成功、RAW SHA-256不変、Ending呼出0。`work/v121-optional-ending-real.json`。
- 正常時のaction deadlineを一律2秒へ短縮する変更は撤回。観測waitだけ2秒slice＋元deadlineとし、副作用actionは繰り返さない。固着actionは所有process fallbackで停止する。
- 上記はsourceの途中結果。package/最終suite/Git Gateの最終判定はまだ更新していない。

追加修正後の全suite: **388 passed / 195.54秒**（`--basetemp work/v121-full-third`）。そのcollection後に追加したGUI停止表示・未知modal recoveryの2試験を含め、対象fileを別途再実行し **27 passed / 14.15秒**（`--basetemp work/v121-extra-final`）。したがって390個の異なるtestがPASSしているが、390件を一括実行した記録ではない。

試験終了後、v121/pytest実行中Python processは0件。差分は作業treeに保持し、まだ本番配布へ反映しない。

既知事項は引き続き `SOURCE_UPLOAD_ERROR_LIVE_RECOVERY: UNVERIFIED_LIVE`。前回のsource upload error復旧例外を今回のmodal/Stop Gateへ拡張しない。

`DJDMAKER_V121_MODAL_SAFE_SHUTDOWN_RESULT: BLOCKED`

## ユーザー指定URLでの自然再現確認（2026-09-07）

既存の隔離Acceptance用専用profileをそのまま使い、次の3回を各12秒観測した。指定URLは正常なNotebookページとして表示された。スクリーンショットでも告知が表示されていないことを確認した。

| 対象 | visible modal | visible scrim | 中央Chat click/focus |
| --- | ---: | ---: | --- |
| 指定 `a6f5f800-1b8f-4b58-a453-46942026d57a` | 0 | 0 | PASS |
| 同じNotebookをreload | 0 | 0 | PASS |
| 別の既存Notebook `77f77189-9548-44f8-9105-32faa5fb20ba` | 0 | 0 | PASS |

今回の観測範囲では自然再現しなかった。告知のclose操作は0回。Chat欄の実クリックとfocusだけを確認し、本文入力・送信・動画生成は行っていない。したがって「dismiss後のChat復帰」をlive PASSにはしない。modal非表示の原因、再表示条件、ユーザー側とのprofile表示状態の一致は不明。

profile/Cookie/local storageの消去・複製・リセット、再現用DOM注入、Notebook/source/artifact削除は行っていない。検証用automation browserは所有managerから終了した。

証跡: `work/v121-modal-user-url/report.json` と同directoryの各before/afterスクリーンショット（ローカル限定・Git対象外）。製品source、version、build、commit、pushの追加変更なし。modal live Gateおよび前節の未完了Gateは引き続き保留。
