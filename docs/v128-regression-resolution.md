# Ver1.2.8 26 FAILED解消・Release検証

指示: DJD-CHAPPY-V128-26FAILED-REGRESSION-RESOLUTION-FULL-001
基準HEAD: 855640e1896f4bfd0a78d921b1c018f9c62641fb / main。
開始時: tracked変更16、untracked5。728件中702 PASS / 26 FAILED。
証跡: work/v128-output-recovery-full-002-suite-{0,1}.xml/log。既存live004は保持。

## 修正前分類（26個別case）

A=明示的な新仕様に反する旧期待、B=実回帰、C=仕様間矛盾（実装対応も必要）。短縮名はtests/test_*.pyに対応。

| # | suite / case | 分類 | expected → actual / call path・遷移 |
|---|---|---|---|
|1|unit4_multijob / three_jobs_keep_identity_raw_immutable_and_zip_mapping|A|delete3 → 0。回収→Completed成功後の旧delete期待。RAW/ZIP対応検証は維持|
|2|unit4_multijob / existing_zip_is_collision_not_another_jobs_mapping|A|MEDIA_STAGE_FAILED → OUTPUT_EXISTING_UNVERIFIED。ownershipがHLS開始前に保留。無断採用/上書き禁止は維持|
|3|v123_generation_first_refresh / 100_jobs_generation_dispatch_before_any_download_or_local|A|events90 →85。submit80/download5成功、廃止delete5だけ不足|
|4|v123_generation_first_refresh / fatal_does_not_block_cloud_phase_and_retryable_processed|C|retry submit期待→fatal checkのみ。旧FATAL無操作期待は新仕様と矛盾、retry停止は実回帰。P1/再評価を修正|
|5|v127_source_recovery / source_retry_budget_survives_restart|B|SourceRetryExhausted→LOCAL_SOURCE_FILE_MISSING。ensure_sourceがbudget3判定前にTXT検証。counter自体は永続保持|
|6|v12_release_gate / 175_resume...[False]|B|resume100→93。旧Quota7件NOT_STARTED+SENT+QUOTA返信の復帰分岐欠落→UNKNOWN|
|7|v12_release_gate / 175_resume...[True]|B|同上。今回replyと旧Quotaを分離してWAITINGへ戻す必要|
|8|v122_sequential_runtime / inspect_then_act...[PRESET_SEND_FAILED]|B|check/submit(a)/check/submit(b)→check(a)のみ。P3再照合WAITING直後return|
|9|v122_sequential_runtime / inspect_then_act...[SOURCE_UPLOAD_FAILED]|B|同上。既知retry可能jobのgeneration候補扱いが欠落|
|10|v122_sequential_runtime / inspect_then_act...[QUOTA_EXHAUSTED]|B|同上。旧Quotaだけで現在送信を抑止しない|
|11|v122_sequential_runtime / ready_artifact_deferred_until_next_job_dispatched|B（副次A）|b生成前aがDOWNLOAD_PENDING期待。全FAILEDのP3移行でb生成に到達しない。両jobは既知Preset retryなのでP1 check-before-actへ復元。後段の旧delete期待だけAとして更新|
|12|v122_sequential_runtime / completed_and_fatal_never_open[FAILED-FATAL_FAILED]|A|events0→check。bare FATALだけの無操作期待は新再照合仕様と矛盾。COMPLETED保護は維持|
|13|v122_sequential_runtime / stop_prevents_next_navigation_and_persists_outcome|B|RunCancelled期待→未発生。submitへ到達せずStop callback未実行。Stop後navigation増加を実証した失敗ではないがcheck→act退行|
|14|v122_sequential_runtime / 100_jobs_simulated_navigation_and_first_action_improve|B|100件check/submit→最初のcheckのみ。WAITING復旧後の実行欠落|
|15|v122_sequential_runtime / real_submit_adapter_source_and_preset_before_next[NO_RESPONSE-MISSING]|B|送信前source復旧期待→NOT_STARTED/sourceMISSINGをUNKNOWN化、送信なし|
|16|v122_sequential_runtime / real_submit_adapter_source_and_preset_before_next[NO_RESPONSE-READY]|B|送信期待→WAITING復帰後return|
|17|v122_sequential_runtime / real_submit_adapter_source_and_preset_before_next[QUOTA_EXHAUSTED-MISSING]|B|旧Quotaから今回source/送信期待→診断分岐欠落|
|18|v122_sequential_runtime / real_submit_adapter_source_and_preset_before_next[QUOTA_EXHAUSTED-READY]|B|今回送信期待→WAITING直後return|
|19|v122_sequential_runtime / cached_fatal_classification_prevents_remote_open|A|check0→check1。cached FATALだけでremote照合禁止という旧期待|
|20|v122_sequential_runtime / media_failure_not_retried_in_same_run_or_foreign_zip_adopted|A|FATAL_FAILED→OUTPUT_BLOCKED。既存foreign ZIPを事前安全保留。再実行/上書き禁止を維持|
|21|v12_resume_gui / failed_resume_by_remote_checkpoint_without_new_job[WAITING-RESERVED_WAITING_CREDIT_RESET]|B|RESERVED_WAITING_CREDIT_RESET→FAILED。新reconcileにWAITING対応なし|
|22|v12_resume_gui / failed_resume_by_remote_checkpoint_without_new_job[NOT_STARTED-WAITING]|B|WAITING→FAILED。inspect-only NOT_STARTED対応欠落|
|23|v12_resume_gui / remote_diagnosis_updates...[diagnosis0-QUOTA_RECOVERY_PENDING]|B|WAITING/Quota分類→FAILED/None。旧Quota診断分岐欠落|
|24|v12_resume_gui / remote_diagnosis_updates...[diagnosis1-PRESET_SEND_FAILED]|B|WAITING/Preset分類→WAITING/None。分類を無条件clear|
|25|v12_resume_gui / remote_diagnosis_updates...[diagnosis2-SOURCE_UPLOAD_FAILED]|B|WAITING/Source分類→FAILED/None。Source ERROR分岐欠落|
|26|v122_recovery_continue / generating_deadline_due_reschedule_then_ready_same_job|A|末尾poll/download/delete→poll/poll/download。期限確認成功後の旧delete期待。その他assert維持|

分類合計（主因）: A 7 / B 18 / C 1。Bの1件は後段に旧delete期待Aも含む。Cは期待変更のみで解消せずpriority/復旧動作を修正する。

## Gate

指定7suiteはwork/v128-regression-seven-002.xmlで125 PASS。初回の123 PASS/2 FAILEDは新FATAL試験に追加した診断分類期待の不整合（None対PRESET_SEND_FAILED）であり、診断原因保持に合わせ修正。旧26件のB期待は弱めていない。

追加所有権監査でCRCだけのZIP採用、同サイズ別RAWの採用、digestのない旧tombstoneによる記録除去を検出。これらも今回明示された安全Gateに合わせ修正する。新helperでsource SHA＋ZIP SHAまたはHLS内容照合を要求し、production/publish/deferredで共通使用。試験fixtureの成功例には実SHA根拠を追加し、根拠なしlegacyは保留を期待する（新Gate由来の変更）。

所有権/deferred/safety関連80 PASS（work/v128-ownership-cross-002.xml）。同サイズ異内容RAW拒否を含むraw suite 6 PASS。
live005は回収・Completed成功後の再照合で試験helperのrecover_page指定欠落により保持確認未完。helperを本番同一browser.restartへ接続し、live006ではREADY→Download→RAW12→Ending skip→HLS/ZIP→Completed、およびDownload後/Completed後READYを確認。送信/upload/delete0、本番JSON1,346件変更0。004/005/006の証跡は上書きしない。
work/v128-regression-recovery-source-001.jsonでSource/Generation retry、Quota回復priority、追加synthetic READY FAILED回収・保持・再Start追加操作0を確認。synthetic remoteをliveと混同しない。

全728以上PASS前にbuild/commit/pushしない。source retry budget、Stop、生成優先、成果物所有権の期待を弱めない。
本番JSON/media/remote動画は変更・削除しない。試験はcopy/fixtureを使用する。

## 追加安全監査とEXE検証履歴

- 全742 tests PASS / failures0 / errors0 / skipped0（53files、`work/v128-regression-final-001-tests.json`）を確認後に正式build実施。PyInstaller onedir、Version1.2.8、ExecutionPolicy拒否なし。
- 初回EXE Recovery/Shutdown PASS。Package408files、CRC/全展開hash一致、runtime/profile実ファイル0。
- EXE isolation初回は2job Completed、隔離/overlay/復旧成功だが試験側に旧delete期待が残っておりfalse。この原因はA（明示的retention仕様と矛盾）。submit/download各1とdelete0、RETAINED必須へ変更。他の隔離・回復・GUI更新assertは維持。source実GUI/mediaで再検証PASS（`work/v128-regression-isolation-source-003.json`）。source002は媒体実行ファイルの配置がないことで起動失敗し、PASS扱いしない。
- EXE lifecycle初回は通常ChromeへのWM_CLOSE後20秒joinの複合assertで停止。ブラウザ本体・試験条件を変更せず、fresh002で再実行。初回ログではworker生存とworker例外を区別できず、並列負荷原因との断定はしない。
- 最終監査で回収専用入口にも所有権再照合を追加。RAWとedited_pathが別jobのRAW/edited_pathに重なる場合もOUTPUT_BLOCKEDとし、二重変換しない。通常開始/回収開始の両経路を追加検証。関連20 tests PASS。default pytest一時folderに対するWinError5の試行は環境setup errorであり、fresh workspace basetempで再実行した。
- 最後の所有権補正前に開始したfull002は最終source一致の証跡とはしない。最終source・EXEの再Gateが必要。

## 最終source Gate

`work/v128-regression-final-003-tests.json`: 全53files / 746 PASS / FAILED0 / ERROR0 / skipped0。
必須suiteはunit4_multijob 9、generation_first_refresh 51、source_recovery 7、release_gate 7、sequential_runtime 38、resume_gui 11、recovery_continue 2、すべてPASS（計125、全746の内数）。compileall / diff --check PASS。
run_cycle / run_recovery_cycleの共有RAW・edited所有権保護4caseを含む最終sourceで再buildする。先行743 PASSも証跡として保持する。
通常sequential source smokeはsubmit2→download2、delete0、2job CompletedでPASS。実GUI/media、remoteはFake。
先行EXE lifecycle002は600.030647秒後の再探索、Pause/Resume、Stop Reason、APP_CLOSE PASS。limit001は610.774483秒後の再探索、上限中Local/Download、Shared Preset PASS（real Chromeのlocal DOM fixtureでありlive quotaではない）。最終buildでも再確認する。

## 最終build / package

- 746 PASS後、既存 `packaging/build_windows.ps1` で再build PASS。PyInstaller6.22.2 onedir、Python3.14.6、assert有効。Version1.2.8 / FileVersion1.2.8.0 / ProductVersion1.2.8。ExecutionPolicy拒否なし。
- Portable: `dist/DJDmaker_Ver1.2.8`。
- EXE: 4,060,530 bytes / SHA-256 `4ECB3A02A652841BC8E1783835B52C4FCA877CB80ECB575FCDA434B2301F9D92`。
- ZIP: `dist/DJDmaker_Ver1.2.8.zip`、277,855,286 bytes / SHA-256 `4F79993195D62F9C4293F2A4DD2DAD15A901E56A10D7C2FD314FBC7F81CE9C84`。
- `work/v128-regression-final-package-report.json`: 408files、ZIP CRC、全展開hash一致PASS。private profile/runtime/media0。日本語＋空白pathへfresh展開済み。
- 先行candidateからのSHA変更理由: sequential smokeのretention期待更新と回収専用入口／cross-job編集動画保護を追加して再buildしたため。先行ZIPはworkへ退避し、正式名と区別する。
- 最終EXE recovery/isolation/shutdown PASS。sequential初回は起動時modal試験中に既存の3秒/workerなし判定が動作し、実処理開始前（calls0、dialogs2）にfalse。条件を変更せず、他の短時間試験終了後に同一EXE/fresh002で再確認する。初回失敗を消さない。
- sequential fresh002は同一EXE/同じtimeoutと判定でPASS（submit2→download2、delete0、Completed2）。日本語＋空白pathに展開したEXEもGUI切替/再起動選択復元とRecovery PASS。`work/v128-regression-extracted-{gui-0,gui-1,gui-2,recovery}.json`。正式portable runtime実ファイル0を再確認し、EXE/ZIP SHAは試験前後で一致。

## 最終技術Gate確定（2026-09-10）

最終EXE lifecycle001: PASS / 600.036898秒で再探索 / Current Window、collision、Pause/Resume、Stop Reason、APP_CLOSE PASS。
最終EXE limit001: PASS / 607.446182秒で再探索 / limit中Local・Download、collection Completed、Shared Preset PASS。
最終EXE recovery/isolation/shutdown、sequential再試験002、GUI3回起動、展開後GUI3回/Recovery、package、全746tests、affected live006の16証跡を照合しPASS。
一括実行wrapperは初回sequential001のfalseを保持するためexit1。判定を緩めず同一EXEで行った単独002のPASSを最終Gateに採用し、初回を削除・成功に書換えない。
本番JSON1,346件は変更0/欠落0/追加0。元3repo変更0、phase2 ref保持、tracked/untracked実media/DB/profile0、高確度credential signature0。
先行の失敗・診断記述は時系列の証跡であり、現行の未解決回帰を意味しない。

TECHNICAL_RELEASE_GATES: PASS
Git commit/push後のSHAと最終判定を含む52項目報告は `work/v128-regression-final-report-20260910.txt` に記録する。
