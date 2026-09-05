# Unit 3 Live Acceptance 完了記録

指示ID: `DJD-CHAPPY-V01-UNIT3-LIVE-ACCEPTANCE-ARTIFACT-DELETE-001`

追加・訂正ID: `DJD-CHAPPY-V01-UNIT3-BROWSER-CONNECTION-CORRECTION-001`

実施日: 2026-09-06

現状判定: `PASS`

1. Repository root: `C:\xampp\htdocs\PHP\DJDmaker`
2. Branch: `main`
3. HEAD before: Unit 2 `424201d59bad0f337c39878e3c81dfc99b2a0106`、Unit 3非live checkpoint `bf7974cf616be7a5eeaf1a8f85c48efb12a7485a`。
4. Commits: Unit 3コード・tests・docsのみローカルcommitする。GitHub pushなし。
5. Git status: runtime Acceptance dataはignore対象。commit対象はコード・tests・docsのみ。
6. 実ブラウザ接続結果: GNB_Creatorと同じ専用profileを通常Chromeの `--user-data-dir` で開いて人間が初回ログインし、Chrome終了後に同じprofileをPlaywright `launch_persistent_context` で再利用。認証状態の引継ぎに成功。Computer Use不使用。
7. 診断TXT: `input/DJD_LIVE_ACCEPTANCE_001.txt` 1件のみ。Git追跡なし。
8. Notebook作成結果: 成功。Notebook ID `2073740b-71ca-4ad7-8cfb-a595ddc50c29`。
9. Rename: `DJD_LIVE_ACCEPTANCE_001` へ変更し、入力値とpage titleをreadback確認。
10. Source投入: `DJD_LIVE_ACCEPTANCE_001.txt` を投入し、source readyを確認。
11. Video生成: 1件だけ生成開始。重複生成なし。完成動画は59秒。
12. Scheduler/監視: 本番default 600秒→120秒は変更せず、Acceptance process内だけ短縮。`generation_started_at`、`last_polled_at`、`next_poll_at` をJSON保存。
13. 完成検出: 成功。元GNB_Creatorの `role=button`、accessible name `再生`、`exact=True` を踏襲。遷移後は2秒間隔・最大60秒で遅延DOMを待つ方式も移植。
14. Download: Web UIのartifact menuから成功。
15. Download検証: temporary終了、MP4存在、size > 0、size stable、ffprobe、video stream、durationをPASS。
16. RAW保存: `raw_files/DJD_LIVE_ACCEPTANCE_001.mp4`、6,252,639 bytesを安全保存。
17. RAW再検証: H.264/AAC、59.698503秒、ffprobe PASS。
18. GNB_Creator削除元実装: HEAD `28bd51dfe2894018bfc9d65a02f219a933199127`、`app/automation/download_manager.py`、`app/automation/video_monitor.py`、`app/automation/notebooklm_page.py`、保存済み `diagnostics/20260905_101556/STATE_10_VIDEO_MENU` を一次資料として確認。
19. Live artifact menu: `artifact-library-item` 内の `その他` から開き、`ダウンロード` markerを持つartifact menuであることを確認。
20. Live削除selector: playable card、`その他/More`、単一menu、`ダウンロード/Download`、`削除/Delete`。
21. Confirmation: optional扱い。Notebook文言を含むdialogは拒否する。今回のlive UIでは安全にartifact削除が完了。
22. Artifact削除結果: 生成済み動画artifactだけをWeb UIから削除成功。
23. 削除成功判定: card消失を確認後、必ずpage refreshして再出現なしを確認。
24. Refresh後確認: refresh前0件、refresh後0件。
25. Notebook残存確認: Notebook名 `DJD_LIVE_ACCEPTANCE_001` を再読込できた。
26. Source残存確認: refresh前後とも `DJD_LIVE_ACCEPTANCE_001.txt` を確認。
27. RAW残存確認: 削除前後のsize、mtime、SHA-256が一致。SHA-256 `be2be79345a7a6e7c1aa23a3673aad5471e70a28f134c3085570a82423670ebc`。
28. 最終音声位置: 55.441542秒。
29. Cut位置: 55.941542秒。最終音声位置+0.5秒と一致。
30. Ending結果: PASS。診断用固定Ending 2秒を結合し、完成MP4はH.264/AAC、57.966667秒。
31. HLS結果: PASS。約6秒segment、`playlist.m3u8` と `segment00000.ts`～`segment00006.ts` を検証。
32. ZIP結果: `output/DJD_LIVE_ACCEPTANCE_001.zip`、4,089,655 bytes。ZIP_STORED、flat構造、CRC、playlist参照整合性をPASS。
33. GUI状態遷移: 永続jobを正式GUIへ再読込し、`○ Notebook完了` → `○ End完了` → `○ HLS/ZIP完了` → `○ 完成` を確認。
34. Freeze: GUI再読込・描画が正常終了。処理中0、Error 0、操作ボタン応答あり。
35. Tests: 専用 `.venv` で `164 passed in 27.38s`。
36. Packaging preflight: Windows、Python 3.14.6、PyInstaller、PySide6 6.11.2、Qt plugins、Playwright、Chrome、FFmpeg/ffprobe 9.0.1、default config、writable dirs、日本語＋空白pathを全PASS。正式bundle/ZIPは未作成。
37. 元3repo変更: 0。元repositoryは変更していない。
38. DB: DJDmakerはDB不使用。JSON永続化のみ。Chrome profile内部DBはruntime ignore対象。
39. Secrets: password、token、Cookieをコード・docs・Gitへ保存していない。アカウント情報を報告へ含めていない。
40. `.gitignore`: TXT、RAW、edited MP4、HLS、ZIP、logs、screenshots、browser profile、system runtime JSONを除外。
41. 未解決事項: Unit 3の必須工程に未解決なし。正式release bundle、署名、clean machine試験は配布Unitの対象。
42. 次Unit推奨: 正式Windows onedir build、allowlist検査、clean Windows 10/11試験、署名・hash・release ZIP作成へ進む。

## GNB_Creator生成完了判定の再調査結果

- `app/automation/video_monitor.py` の `detect_state()` はartifactが複数なら停止し、`get_by_role("button", name="再生", exact=True)` が可視なら `GENERATION_COMPLETED` と判定する。
- `app/config/selectors.py` の `VIDEO_PLAY_BUTTON_NAME` は `再生`。
- remote recoveryはNotebook遷移後、2秒間隔・最大60秒でread-only状態判定を繰り返す。失敗履歴処理では先に最大30秒 `artifact-library-item` の出現を待つ。
- DJDmakerで見逃した原因は完了selectorではなく、`domcontentloaded` 直後に1回だけ判定し、Angularのartifact cardがmountする前の0件を読んでいたこと。
- DJDmakerへ同じ遅延DOM待ちを移植し、現行live DOMの `aria-label="再生"` で完成検出できた。

## 追加したbrowser・DOM互換修正

- 初回ログインはPlaywrightを介さない通常Chrome、運用は同一profileのPlaywright persistent contextに分離。
- 現行ホームの `新規作成` fallbackを追加し、`/notebook/creating` を確定Notebook IDとして保存しない。
- GNB_Creatorと同じ、source dialogを開いてfile inputまたはfile chooserへ投入するfallbackを移植。
- 現行の `ショート動画の概要を生成しています` をactive markerへ追加。
- 生成artifactのtitleがNotebook titleと異なる場合、元GNBと同じ「唯一のplayable card」fallbackでDownload対象を限定。
- 完成検出からDownload・削除まで、同じmount済みpageを再利用。

`UNIT3_RESULT: PASS`
