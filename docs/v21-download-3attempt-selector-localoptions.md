# Ver2.1 Download 3-attempt / Notebook selector / Local options release report

- 返答ID: `DJD-CODEX-20260930-V21-DOWNLOAD-LOCALOPTIONS-001`
- 指示ID: `DJD-CHAPPY-V21-DOWNLOAD-3ATTEMPT-SELECTOR-LOCALOPTIONS-FULL-002`
- Repository: `C:\xampp\htdocs\PHP\DJDmaker`
- Branch: `main`

1. HEAD before: `b9441b687df7bc3e1ba8500919c5e035ca460a5f`（開始時 `origin/main` と一致、0/0）。
2. 不完全Download root cause: 完成ファイルの存在を先に採用でき、10 MiB未満を再取得する回数制御と永続checkpointがなかった。
3. size Gate: `10 MiB = 10,485,760 bytes` を厳密に使用。
4. attempt 1: 10 MiB未満なら未完了として再Download。
5. attempt 2: 10 MiB未満なら最後の再Download。
6. attempt 3: size retryを終了。10 MiB以上は通常通過、未満は `ACCEPTED_AFTER_3_SMALL_DOWNLOADS` として動画検証へ進む。
7. 4回目防止: attempt数をjob JSONへ先に保存し、再起動・手動retry後も4回目を開始しない。
8. small-file accept仕様: 3回連続smallだけでは正常扱いにせず、size gateだけを打ち切る。
9. RAW validation連携: ffprobe、container、video stream、duration、RAW safety gateを全通過後にのみ `DOWNLOAD_VALIDATED`。
10. retry永続化: `download_attempt_count`、`small_download_attempts`、size/validation statusを保存。
11. atomic replace: attempt一時fileを検証対象へ `os.replace`。`(1)`、`(2)`、`.crdownload` を完成物扱いしない。
12. Chrome再発root cause: current state以外の過去open/close情報を判定に混ぜられる余地があった。
13. history依存除去: production current-window判定は過去history、last_closed、previous_session等を参照しない。
14. Current Window実装: 現在のAUTH/automation Chrome process、window、profile lock、DJDmaker所有processのみを使用。
15. Notebook selector旧原因: Create候補が現行のaccessible name/semantic DOMを十分に包含していなかった。
16. 現行Notebook UI: 日本語/英語accessible name、role、aria-label、visible text、home領域を調査。
17. 新selector: 現行日本語名、英語名、role button/link、`main`配下aria semantic候補を追加。
18. selector fallback: role + accessible name → aria-label → visible text → stable semantic containerの順で共有。
19. DOM wait: visibleかつenabledな候補だけを採用し、home URLを確認してからclick。
20. modal guard: create直前に既存modal dismissal/interaction guardを通過。
21. Notebook create live: PASS。実profileでHome → Create → Notebook openまで確認。
22. Notebook ID保存: `b12b14ff-9605-4994-a968-d5ed9d051d08`。
23. Notebook URL保存: `https://notebook.google.com/notebook/b12b14ff-9605-4994-a968-d5ed9d051d08`。source未投入、削除せず保持。
24. Ending分離: `ending_enabled` を独立化。OFFは `SKIPPED_BY_SETTING`。
25. Tail Cut分離: `tail_cut_enabled` を独立化。最終有音位置 + 0.5秒方式を維持。
26. Encode分離: `encode_enabled` を独立化。OFF時に暗黙の全面re-encodeをしない。
27. 3 settings key: `ending_enabled`、`tail_cut_enabled`、`encode_enabled`。
28. migration: 旧settingsでEnding pathありは3項目ON、なしは3項目OFF。推測で別値に分けない。
29. Phase1 GUI: 3項目を個別checkbox化し、処理中は変更不可。
30. Phase2 GUI: 同じSettings/ViewModelを共有。
31. shared settings: GUI別storageを作らず、同一設定を永続化。
32. run snapshot: Start時に3項目とHLS/ZIPをjob/runへsnapshotし、実行中変更を混在させない。
33. 8 patterns: `000/100/010/001/110/101/011/111` を全PASS。
34. 16 HLS/ZIP contracts: 8 local patterns × HLS/ZIP ON/OFFを全PASS。
35. Ending call counts: 各patternでON時のみ1、OFF時0を検証。
36. Tail Cut call counts: 各patternでON時のみ1、OFF時0を検証。
37. Encode call counts: 各patternでON時のみ1、OFF時0を検証。
38. final MP4: HLS/ZIP OFF時も完成MP4を保持しCOMPLETED可能。
39. RAW保持: live A/B/Cで入力RAW SHA-256 `6daa0a8e1576b14f299d2ca4bdadbe247fa9f36539733158d7fa4995c445feb0` 不変。
40. remote artifact保持: Studio Delete call 0。Notebook artifact自動削除なし。
41. tests before: 正式Ver2.0 baseline 821 passed。
42. tests after: 852 passed（2026-09-30、302.33秒）。
43. new tests: Download最大3回/再起動/atomic、current-window、selector、settings移行/保持、8/16 patternsを追加。
44. regression: compileall PASS、既存Cloud/Quota/Pause/Stop/Dual GUI/JobStateSave/HLS等を含む全suite PASS。
45. Download live: 許可された `C:\Users\Ichiro\Desktop\test_list` の台本をコピー利用。3 small validはvalidation後COMPLETED、brokenはFAIL、再試行後の新規download 0。
46. Chrome live: production BrowserManagerと専用profileによる現行sessionでNotebook Createを実施。history不使用。
47. Notebook selector live: 現行日本語Create buttonでHome → open → ID/URL取得PASS。
48. local setting live: A=`111`、B=`011`、C=`000` PASS。B 20秒、A 23.033333秒。
49. HLS/ZIP live: BでONはZIP作成（688,976 bytes）、OFFは完成MP4を選択。両方PASS。
50. 本番JSON変更数: 0。試験stateは `work` 配下のみ。
51. Version: Python/Product `2.1.0`、FileVersion `2.1.0.0`、GUI `Ver2.1`。
52. cleanup: 旧配布物・旧checksum・各正式build後の再生成可能PyInstaller中間物のみ17処理対象、454 files、1,007,452,308 bytes削除。失敗0。Ver2.1/source/Git/user data/build資材保持。
53. build: 既存 `packaging/build_windows.ps1`、PyInstaller onedir PASS。
54. portable: `dist\DJDmaker_Ver2.1\`。EXE 4,091,746 bytes、SHA-256 `19C068703823B2F85DCAD39B983D8CB0DBAF166601FE5FBD1959A64710C7A4C3`。
55. ZIP: `dist\DJDmaker_Ver2.1.zip`、272,175,198 bytes、SHA-256 `FF48DA0AE951D3805DEE00885E1A371337FBD86ED3DE2DA85F4DE87D173B2C84`、integrity PASS。
56. EXE Acceptance: GUI/FFmpeg/ffprobe/settings restart/browser/preset/Fake E2E PASS。日本語＋空白Fresh展開先でもPASS。
57. package audit: package/ZIPともChrome profile・Cookies・Login Data・Web Data・History・Local State・runtime job/media/log 0。
58. commit: Gate完了後 `release: DJDmaker Ver2.1` を通常commit予定（実SHAは完了報告で記録）。
59. push: Gate完了後 `git push origin main` の通常push予定。force/rebase/reset/amendなし。
60. HEAD/origin after: push後に一致を確認して完了報告へ記録。
61. ahead/behind: push後 `0/0` を確認して完了報告へ記録。
62. Git status: push後cleanを確認して完了報告へ記録。
63. known issues: release blockerなし。live Createで作成した空Notebookはremote artifact保持方針に従い削除していない。
64. 未解決事項: なし。

最終判定はcommit/push後の照合完了をもって確定する。
