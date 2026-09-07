# DJDmaker Ver1.2.2 最終release検証

返答ID: DJD-CODEX-20260907-V122-FINAL-RELEASE-001

今回指示ID: DJD-CHAPPY-V122-FINAL-RELEASE-BUILD-COMMIT-PUSH-FULL-001

Repository: `C:\xampp\htdocs\PHP\DJDmaker` / branch `main` / 実施日2026-09-07。

本書はcommit前に確定した検証結果。commit/pushの確定SHA・remote照合結果は最終返答に記載し、本書自体へ自己参照SHAを書き戻すためのamendは行わない。全検証Gate PASS後に本書を含むrelease commitを作成する。

## 完了報告58項目

1. HEAD before: `7e5d8ca900926320f50a5ad4dcb218a73ba36b50`。fetch後origin/mainも一致。
2. Sequential architecture: 1job check→decision保存→act→waiting/completed保存→next。保存済みローカルRAWの既存FFmpeg並列laneは保持。
3. Full pre-scan: 通常GUI Startからの全件remote pre-scanなし。`begin_run()`はlocal bookkeepingのみ。失敗jobの診断は現在job IDを指定して実行。互換helperの全件指定テストは通常Start経路と区別。
4. Runtime GUI: 白GUIの現在Job/Notebook/phase/stage/decision/next action/outcome/attempt/elapsed/count、日本語messageをsourceとEXE双方で確認。HUD/Phase2統合なし。
5. Silent skip: ACTION/状態遷移/明示理由を保存して次へ。completed/fatal/期限前待機に説明あり。
6. No-op fail-fast: 3件連続NO_OP_JOB_TRANSITIONでworker安全停止。fixture PASS。
7. Waiting scheduler: 既存`next_poll_at`が要求のnext_check_atに相当。期限前open0、due確認、未完成時再保存。live実測は回収報告参照。
8. Completed artifact flow: HT075 liveでREADY通知から約0.456秒後にDownload開始、別Notebookへ移らず同job完走。今回のEXEでも2件をcheck0→download0→delete0→check1→download1→delete1で実行。
9. RAW gate: 既存12項目を維持。HT075 live 12/12、未達のartifact削除禁止fixture PASS。
10. Ending optional: 未選択はSKIPPED、検証済みRAWをそのままHLSへ渡す。選択ありの既存Ending E2EもPASS。
11. HLS: 実FFmpeg libx264/AAC、既存6秒設定、playlist/segment/ffprobe検証PASS。
12. ZIP: runtime成果物のZIP_STORED/integrity/上書き禁止を維持。配布ZIPは別用途のZIP_DEFLATED。
13. TXT move: COMPLETED後移動、起動/Start時補完、collision非上書き、失敗時COMPLETED維持。source fixtureおよび2件EXE E2EでMOVED。
14. Completed再生成: HT075の実GUI再StartでNotebook操作/Preset送信/再生成0、RAW/ZIP/TXT不変。今回は同source経路を維持し追加live生成なし。
15. Stop: source実GUI smoke 4.4984秒、EXE 3.3083秒。前回の約1.86秒と同一測定値ではない。今回は実Chrome cleanupを含むローカルfixtureで、Stop後追加navigation0・worker終了・owned process0を確認。
16. Close: source実GUI 1.5401秒、EXE処理中Window Close 1.2405秒。shutdown error0、worker終了、browser cleanup、orphan0。
17. Modal fixture: detect/classify/scoped dismiss/scrim消失/Chat click復帰PASS。ACTION_CONFIRMATION/UNKNOWN非dismiss、Stop中追加dismiss0も全suiteでPASS。
18. `ANNOUNCEMENT_MODAL_LIVE_AUTO_DISMISS: UNVERIFIED_LIVE`。自然出現なし・強制再現なし。fixture成功をlive成功へ読み替えない。
19. Quota fixture: 古い返信を現在のQuota不足としない。current attempt replyだけの判定、retry・reservation分岐PASS。
20. Reservation: current Quota reply→予約→SCHEDULED_REMOTE→RESERVED_WAITING_CREDIT_RESETのfixture PASS。TAX120の旧Quota→current retry→生成開始live証拠は引継ぎ。今回はQuota消費による強制再現なし。
21. `SOURCE_UPLOAD_ERROR_LIVE_RECOVERY: UNVERIFIED_LIVE`。原因不明のアップロード回復を修正成功にしない。
22. Recovery: 生成済み/予約/待機/Download待ちだけ。生成前retryはStart側。回収ボタンからsubmit/create/upload/generateしない回帰PASS。
23. Delete: 複数選択/完成一括のjob record削除を保持。RAW/TXT/HLS/ZIP/Notebook/source/artifact保持のfixture PASS。
24. Sort: 全dataset natural ASC/DESC、選択job ID維持の回帰PASS。
25. 175 migration: source実データcopyで175、COMPLETED75/FAILED100、全ID・snapshot・Notebook保持、2回目migrated0。EXE起動migrationでも同数・全ID保持・175件backupあり。
26. 本番177 JSON変更: **0**。最終監査でもcopy前backupと全177ファイルのSHA辞書完全一致。
27. Tests: Version変更前443 passed /247.05秒。Version更新・検証入口追加後 **444 passed /228.87秒**（一括suite）。追加1件は明示opt-inなしでは検証runtimeを作成しないことを確認。
28. compileall: `python -m compileall -q src tests packaging` PASS。
29. diff check: PASS（Windows CRLF変換予告のみ）。
30. Version: Python/project 1.2.2、FileVersion 1.2.2.0、ProductVersion 1.2.2、GUI「台本から授業動画つくるマシーン Ver1.2.2」。実EXE resource確認済み。
31. Cleanup: 下表の再生成可能物2,944,158,090 bytesの削除を試行したが環境ポリシーが実行前に拒否。削除0、迂回0、今回指示の例外に従い保持。ユーザーデータではないことを確認。
32. Portable path: `C:\xampp\htdocs\PHP\DJDmaker\dist\DJDmaker_Ver1.2.2\`。
33. EXE size: **3,888,294 bytes**。
34. EXE SHA-256: `5C90B31739F5C1A0232F03E12C69C0182106EC0BB33B32A9AFDCB2C73A7FAAC2`。
35. ZIP path: `C:\xampp\htdocs\PHP\DJDmaker\dist\DJDmaker_Ver1.2.2.zip`。
36. ZIP size: **277,668,187 bytes**。
37. ZIP SHA-256: `8D579E60F3D0D7A4B3EA3B223240C16E5597B7F53DBED8F6FDC6B417170431B3`。
38. Checksum file: `dist\DJDmaker_Ver1.2.2_SHA256SUMS.txt`（EXEとZIPの64桁SHA）。
39. EXE Runtime GUI: 全10fieldと日本語message表示、現在job/工程/判断/次処理更新を実EXE screenshotとイベント列で確認。
40. EXE sequential: 2jobsともCOMPLETED。先行jobのDownload/削除/HLS/ZIP/TXT移動後に次job。Main GUI/Settings/Preset/JobDetail/Log実表示PASS。
41. EXE Stop: 3.3083秒、navigation0→0、worker false、owned process0、errors0。
42. EXE Close: 1.2405秒、navigation0→0、worker false、owned process0、errors0。
43. EXE Ending optional: 2件SKIPPED、HLS PASS、COMPLETED、TXT MOVED。別の既存Fake E2EはEnding選択ありでPASS。
44. EXE migration: 新規ZIP展開先へ175件copyし、実EXE起動・migration・正常終了を検証。175/75/100、全ID/snapshot/Notebook保持。
45. ZIP展開後: 日本語＋空白pathでGUI/settings save→process再起動read/preset/browser/FFmpeg/ffprobe/Fake E2E/sequential/Stop/Close/migrationすべてPASS。追加Google login・動画生成なし。
46. Package audit: pristine portable408files。browser profile/Cookie/Login Data/Local State/credentials/runtime state/本番JSON/RAW/HLS/outputZIP/logs/実データ画像/移行copy0。全ファイルを監査後ZIPへ格納、CRC integrity PASS。Playwright `.d.ts`はTypeScript定義でHLS動画ではない。依存内token/session等の実装名をユーザー認証情報と混同しない。Git tracked DB/media0、secret keywordは防御コード・fixtureのdummy値・文書のみ。
47. Release docs: 本書、README更新、開発ルール、先行live2報告を含む。証拠/試験profile/mediaはwork内Git ignored。
48. Commit: 全Gate確認後、本書を含む`release: DJDmaker Ver1.2.2`をmainへ作成。確定SHAは最終返答/Git履歴。
49. Push: 対象remote `https://github.com/G-fontis/DJDmaker.git`、通常`git push origin main`のみ。結果はpush後の最終返答。
50. HEAD after: 本書を含むrelease commit。自己SHAの追記/amendはしない。
51. origin/main: push後fetchとGitHub `ls-remote refs/heads/main`でlocal一致を検証して最終返答。
52. ahead/behind: push前はbehind0を確認、push後0/0を確認して最終返答。
53. Git status: 配布物・workはignored。対象source/docs/tests/build設定だけcommitし、push後cleanを確認して最終返答。
54. Phase2変更0: ref `46c4599e76383b663552aec3f052e0dca8cc09f9`維持。phase2-v12作成/統合0、旧Phase2配布物にも未変更。
55. Phase3作成0。
56. Known issues: modal live/source upload error liveは上記UNVERIFIED_LIVE継続。旧build cleanup policy拒否は承認範囲の保持例外。
57. 未解決事項: 新たな重大機能問題なし。HT075の本番state引継ぎ注意は回収報告参照（copy側のみCOMPLETED、remote artifact削除済み、本番JSON未更新）。
58. Release readiness: source/live引継ぎ/EXE/package Gate PASS。通常commit/push後の最終判定は最終返答に記録。

## Build方式と再現

PyInstaller 6.22.2 / Python3.14.6、既存`packaging/DJDMaker.spec`のwindowed onedir、Qt/PySide6/Playwright nodeとFFmpeg/ffprobe9.0.1を同梱。新UI/theme追加なし。

`build_windows.ps1`直接実行はWindowsのscript execution policyにより拒否されたため、policyを変更せず、同じpreflight/specのPython CLIを実行した。旧build cleanup拒否を迂回せず、新規`build/pyinstaller-v122`をworkpathとした。specのruntime/config/licensesを既存scriptと同じ配置へ移し、空runtime folderを作成してrelease-tree preflight PASS。

build後に製品sourceを変更していない。追加の`--packaging-sequential-smoke`は明示`DJD_PACKAGING_SMOKE=1`時だけの検証用入口で、通常起動/Google処理は変更しない。Fake remoteと実GUI・媒体adapterを組み合わせ、隔離rootでのみ処理する。

## Cleanup対象と最終容量

| 拒否された対象 | bytes |
| --- | ---: |
| dist/DJDmaker_Ver1.1 | 701,277,060 |
| dist/DJDmaker_Ver1.2 | 701,303,594 |
| dist/DJDmaker_Ver1.2.1 | 701,332,901 |
| dist/DJDmaker_Ver1.1.zip | 271,708,073 |
| dist/DJDmaker_Ver1.2.zip | 277,615,541 |
| dist/DJDmaker_Ver1.2.1.zip | 277,645,184 |
| build/pyinstaller | 13,275,737 |
| 合計（実削除0） | 2,944,158,090 |

各portableのsystem/browser/raw_files/output/input/logs/work内ファイル0。旧checksumとGit source履歴を保持。Ver1.2.1 sourceはHEAD before、旧EXE SHA `ADFDB37C8DE7F0B802A058A84D6D4503C40A08ECCA204E70992AA499E8459942`、旧ZIP SHA `7C7FAB8F449CFB175F4AD25A4730DC29B1485FAF09482ECD5EB7436B5FDCB7AB`。

最終dist容量 **4,883,131,235 bytes**（旧版/変更禁止Phase2/新版/各checksum含む）、build容量 **26,658,644 bytes**。ユーザーDATA/認証profile/HT075回収RAW・ZIPは削除しない。再生成可能な旧物を保持した理由を容量削減成功と誤記しない。

## ローカル証跡

- `work/v122-package-report.json` / `work/v122-final-audit.json`
- `work/v122-portable-verified.json`
- `work/v122-exe-sequential-report.json` / `work/v122-exe-sequential-01/runtime.png`
- `work/v122-exe-shutdown-report.json`
- `work/v122-portable-migration-report.json` / `work/v12-final-migration-report.json`
- 展開先: `work/v122 配布検証 ys17wey8/DJDmaker_Ver1.2.2`
- migration展開先: `work/v122 移行検証 agxkl95q/DJDmaker_Ver1.2.2`
- Source suite: `--basetemp=work/v122-release-full-01`（443）、`--basetemp=work/v122-release-full-02`（444）
- Live引継ぎ: [逐次送信](v122-sequential-resume-runtime-modal.md)、[逐次回収](v122-sequential-recovery-live.md)
