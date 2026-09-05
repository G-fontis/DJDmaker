# Unit 2 実装記録

指示ID: `DJD-CHAPPY-V01-UNIT2-GUI-SCHEDULER-ENGINE-FIDELITY-001`

補正指示ID: `DJD-CHAPPY-V01-UNIT2-ENGINE-FIDELITY-PATCH-001`

判定: `PASS_WITH_LIVE_DELETE_PENDING`

1. Repository root: `C:\xampp\htdocs\PHP\DJDmaker`
2. Branch: `main`
3. HEAD before: `a69ada06acb05c5970b2549db070da343dcbd669`。HEAD afterはUnit 2ローカルcommit。
4. Commits: Unit 2完了時にローカルcommitを1件作成。GitHub pushなし。
5. Git status: commit前にtracked/untracked/ignoredを確認し、runtime生成物をcommit対象外とした。
6. PySide6/runtime: プロジェクト専用 `.venv` を作成。CPython 3.14.6、PySide6/Qt 6.11.2、Qt plugins、Qt Multimedia、FFmpeg/ffprobe 9.0.1、Windows shell open、Unicode日本語pathがPASS。
7. GUI実装: `src/djd_maker/gui` に正式PySide6 GUIと起動compositionを実装。
8. メイン画面: 正式名称、3エンジン構成、`Created by 福ゼミ塾長`、各path、Ending、開始・一時停止・停止・再読込・詳細・ログ・設定を表示。
9. ジョブ一覧: `No / 台本名 / Notebook / End処理 / HLS/ZIP / 状態` の固定6列。`○ / ▶ / － / ×`を文字と併用。
10. 設定: 初回600秒、通常120秒、余白0.5秒、FFmpeg同時数、各pathをJSON保存・復元。Ending未設定時は開始不可。
11. ジョブ詳細: Notebook情報、scheduler時刻、RAW path/size/duration/codecs、最終音声/cut、Ending/HLS/ZIP、state/errorと安全な再実行buttonを実装。
12. ログ: 時刻、job、engine、stage、level、messageと複合filterを実装。Cookie/token/credentials/session等をredact。
13. Scheduler: `PersistentPollScheduler` が初回600秒、以後120秒のdeadlineをjob JSONへ永続化。
14. Restart/sleep: 既存deadlineを維持し、遅延復帰時は即時1回だけpoll。次deadlineは実poll時刻から設定し、負の残時間とcatch-up burstを防止。
15. Pipeline接続: `GuiPipelineController` と `build_desktop()` がJSON repositories、scheduler、Notebook、RAW、Ending、HLS、Pipeline、GUIをcompositionする。
16. Non-blocking: Qt `QRunnable/QThreadPool` と専用Pipeline workerを使用。Playwrightは利用thread内で遅延生成し、UI threadでbrowser/FFmpeg/file/HLSを実行しない。
17. Pause/resume/stop: scheduler deadlineを変更せずpause/resumeし、stop/shutdown時はworkerとbrowser contextを安全に終了。
18. Ending preview: Qt Multimediaを第一候補にし、終了/エラー時にsourceを解放。不正media時のみWindows既定openへfallback。
19. GNB_Creator削除元実装の所在: `diagnostics/20260905_101556/STATE_10_VIDEO_MENU/{page.html,elements.json,accessibility.txt}` と `app/tools/notebooklm_diagnostic.py --probe-video-menu`。HEAD `28bd51dfe2894018bfc9d65a02f219a933199127`。
20. 元実装の処理順: 完成動画artifact cardを確認し、card内「その他」を開き、表示menuを診断。保存済みDOMに「ダウンロード」と「削除」がある。productionの `NotebookDeletionService` は別系統のNotebook全体削除であり移植していない。
21. 元selector/action sequence: `artifact-library-item`、card内button `再生`、card内button `その他` (`artifact-more-button`)、開いたrole `menu`、role `menuitem` name `削除`。
22. DJDmakerへの移植内容: playable動画cardの一意scope、日本語/英語fallback、単一menu限定、任意dialog、Notebook文言拒否、card消失または明示toast確認、retryable fail-closed。
23. Live Gemini Notebook確認: 保存済み2026-09-05 live DOMとの互換を確認。現行サイトへの再接続は利用可能browser sessionが0件のため未実施。
24. Artifact削除結果: fixture上でaction sequence、dialogあり/なし、fallback、成功、未確認retryをPASS。現行liveでの破壊操作は未実施。
25. Notebook削除との区別根拠: 通常interface、Pipeline、Fake、GUIのいずれにも `delete_notebook` はない。Notebookを示すconfirmationはconfirmせず中止する。
26. 削除成功判定: 対象card消失、または明示設定した新規toastのみ。clickだけでは成功にしない。
27. Fake GUI E2E: PySide GUI → async bridge → scheduler → Pipeline → `FakeNotebookAdapter` → RAW → Ending → HLS/ZIP → COMPLETEDを確認。
28. Tests: 専用venvで `131 passed`。既存84件を含み退行なし。
29. 元3repo変更: 0。3repoとも調査後clean。
30. DB: アプリ実装はDB不使用。JSON repositoryのみ。
31. RAW safety: 12点gateが全PASSした後だけartifact削除へ進む。RAWはno-overwriteで保持し、失敗時も削除しない。
32. Secrets: 実秘密値なし。browser profile、Cookie、session、logsはignore。テスト用dummy文字列だけをredaction testで使用。
33. `.gitignore`: `.venv`、system runtime、browser、logs、input、raw_files、output、work、media、DB、secretsを除外。
34. 元実装から落とした機能: 本アプリ対象の3エンジン機能について0。Notebook本体削除は確定仕様で禁止されているため意図的に非到達。
35. 元3repo検索後も不明だった機能: artifact削除confirmationの現行文言、成功toast、2026-09-05以降のGoogle UI変更。
36. エスカレーション要否: 現行live適合確認にはbrowser sessionと重要データを含まない診断用Notebookが必要。CAPTCHA/認証は人間操作を要求する。
37. 未解決事項: 現行Gemini Notebook上でのartifact削除・card消失の最終確認だけがpending。他機能は実装・自動検証済み。
38. 次Unit推奨: 診断用Notebook 1件で現行DOMを最小確認し、必要なfallbackだけを更新。その後Windows配布packagingと実運用smoke testを行う。

## Artifact削除安全順序

Download → temp終了 → MP4存在 → size正 → size stable → ffprobe → video stream → duration → RAW保存 → RAW存在/size → RAW ffprobe → Web UI artifact削除、の順序を維持する。

## テスト分類

- GUI identity、固定table、設定、詳細、完了表示、button gate、log sanitization、Unicode path
- scheduler 600/120、restart、late/sleep、clock drift、pause/resume/stop、重複poll
- GUI非blocking、worker error route、retry mapping、Fake Notebook GUI E2E
- artifact 12点gate、selector fallback、action sequence、任意dialog、Notebook拒否、成功判定、retryable failure
- BrowserManager dedicated profile、runtime、既存Unit 1 media/repository/pipeline E2E

`UNIT2_RESULT: PASS_WITH_LIVE_DELETE_PENDING`
