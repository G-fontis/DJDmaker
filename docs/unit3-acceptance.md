# Unit 3 Live Acceptance記録

指示ID: `DJD-CHAPPY-V01-UNIT3-LIVE-ACCEPTANCE-ARTIFACT-DELETE-001`

現状判定: `WAITING_FOR_HUMAN_BROWSER_AUTH`

1. Repository root: `C:\xampp\htdocs\PHP\DJDmaker`
2. Branch: `main`
3. HEAD before: `424201d59bad0f337c39878e3c81dfc99b2a0106`。HEAD afterはUnit 3非live checkpoint commit。
4. Commits: 非live実装・tests・packaging docsをローカルcommitする。GitHub pushなし。
5. Git status: commit前安全監査後にclean化する。runtime Acceptance dataはignore対象。
6. 実ブラウザ接続結果: Codexから利用できるブラウザセッションが0件で、Gemini Notebook接続前に待機。認証回避なし。
7. 診断TXT: `input/DJD_LIVE_ACCEPTANCE_001.txt` をruntime dataとして用意。Git追跡なし。
8. Notebook作成結果: live未実施。
9. Rename: live未実施。adapter/testではTXT stemへのrenameを維持。
10. Source投入: live未実施。診断TXTは準備済み。
11. Video生成: live未実施。大量生成なし。
12. Scheduler/監視: default 600秒→120秒を維持。restart/late/pause/resume/重複防止tests PASS。
13. 完成検出: fixture regression PASS、live未実施。
14. Download: fixture regression PASS、live未実施。
15. Download検証: temp除外、存在、size、stable、ffprobe、video、durationを維持。
16. RAW保存: no-overwrite/atomic publish fixture PASS、live未実施。
17. RAW再検証: size/ffprobe gate fixture PASS、live未実施。
18. GNB_Creator削除元実装: HEAD `28bd51d...` の `diagnostics/20260905_101556/STATE_10_VIDEO_MENU` とdiagnostic helperを再確認。
19. Live artifact menu: 保存済みlive DOMとの互換PASS。現行live未実施。
20. Live削除selector: `artifact-library-item`、playable card、`その他/More`、単一menu、`ダウンロード/Download` marker、`削除/Delete`。
21. Confirmation: dialogなし、日本語動画dialog、英語動画dialog、Notebook文言拒否をfixture検証。
22. Artifact削除結果: fixtureでは成功/不確定retryをPASS。現行live未実施。
23. 削除成功判定: card消失後に必ずrefreshし、再出現なしを確認。toastだけでもrefresh必須。
24. Refresh後確認: 再出現時はretryable fail-closedとなるtest PASS。現行live未実施。
25. Notebook残存確認: `delete_notebook` 非公開、project menuをDownload markerで拒否。live未実施。
26. Source残存確認: source削除APIなし。live未実施。
27. RAW残存確認: remote delete成功/失敗/retryの全fixtureでbytes、size、mtime不変。live未実施。
28. 最終音声位置: 元エンジン互換test PASS。live値は未取得。
29. Cut位置: 最終音声+0.5秒policy test PASS。live値は未取得。
30. Ending結果: regression PASS。live未実施。
31. HLS結果: H.264/AAC、約6秒、playlist/segment検証PASS。live未実施。
32. ZIP結果: ZIP_STORED、flat、CRC/integrity regression PASS。live未実施。
33. GUI状態遷移: Fake GUI E2Eとprogress/state/scheduler制御PASS。live未実施。
34. Freeze: background controller/Qt signals test PASS。live未実施。
35. Tests: 専用 `.venv` で `161 passed`。
36. Packaging preflight: PyInstaller 6 onedir候補、PySide6/Qt plugins、Playwright、FFmpeg/ffprobe、Chrome、default JSON、writable dirs、日本語+空白pathが全PASS。正式bundle/ZIPなし。
37. 元3repo変更: 0。すべてclean。
38. DB: アプリはDB不使用。JSON永続化のみ。
39. Secrets: 実値なし。最小selector fixtureのみを追跡し、Cookie/profile/sessionを追跡しない。
40. `.gitignore`: TXT runtime copy、RAW、edited、HLS、ZIP、logs、browser data、screenshots、secretsを除外。
41. 未解決事項: 現行Gemini Notebookへの接続と、診断用1jobのlive E2E全工程。
42. 次Unit推奨: ブラウザを1セッション接続後、このUnitを継続し、診断Notebook 1件だけでlive E2Eを完了する。

## Unit 3で追加した非live安全策

- Pipeline境界でも12点gateを再検証し、不完全gateを弱いadapterへ渡さない。
- Retry時にも永続gateを再検証する。
- Artifact menuに保存済み診断由来のDownload markerを要求し、Notebook project menuを拒否する。
- 削除後は必ずpage refreshし、対象artifactの再出現を拒否する。
- RAWの内容、size、mtimeがremote cleanupの成功・失敗・retryで変化しないことを検証する。

## 人間操作後の継続条件

Codexから利用可能なブラウザセッションを1件接続し、そのブラウザでGemini Notebookへログインする。CAPTCHA、2FA、本人確認が表示された場合だけ人間が完了し、診断用データ以外の重要Notebookを開かずに再開を通知する。

`UNIT3_RESULT: WAITING_FOR_HUMAN_BROWSER_AUTH`
