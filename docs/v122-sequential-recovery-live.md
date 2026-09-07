# Ver1.2.2 candidate: sequential recovery live acceptance

返答ID: DJD-CODEX-20260907-V122-RECOVERY-LIVE-001

今回指示ID: DJD-CHAPPY-V122-SEQUENTIAL-RECOVERY-LIVE-CONTINUE-FULL-001

実施日: 2026-09-07。以下の時刻はJST（UTC+09:00）。Repository: `C:\xampp\htdocs\PHP\DJDmaker`、branch: `main`、HEAD: `7e5d8ca900926320f50a5ad4dcb218a73ba36b50`。未commit sourceでの実GUI・既存Chrome/Playwright・実remote・実FFmpeg試験であり、新portableの検証ではない。build / commit / push / version変更なし。

## 19項目の結果

1. 対象job: TAX120とHT075の保存済みWAITING_VIDEOをcopy側へ移して定時確認。先にREADYを検出したHT075（`481465e9f15e42c2b49d0d7a326cce53`）だけを回収完走。Notebook: `https://notebook.google.com/notebook/a6f5f800-1b8f-4b58-a453-46942026d57a`。
2. next_check_at: 実保存フィールドは既存の`next_poll_at`。初期値TAX120 21:18:34.414274、HT075 21:20:50.416874を変更せず開始。確認後は120秒後を保存。
3. 実再確認時刻: 下表。すべて期限到達後。待機中に期限前Notebook openなし。1件の生成を待ち続けず次jobへ進んだ。
4. artifact: HT075は21:20:50にGENERATING、21:22:50の再確認でREADY。TAX120は最後の21:22:30確認時GENERATING。HT075完走後に試験をStopしたためTAX120を追加回収していない。
5. Download: READY通知から約0.456秒後に同jobの`download.start`。その間の別Notebook openなし。Preset再送・Generateなし。実MP4を既存download adapterで取得。
6. RAW: 46,272,674 bytes、503.176417秒、H.264/AAC。既存12項目Gateすべてtrue。一時downloadと最終RAWのSHA-256一致。HLS/ZIP完了後もRAW保持。Gate後に実Web UI artifact削除を実行し、既存adapterの対象消失・reload後不在検証を通過（REMOTE_ARTIFACT_DELETE_FAILEDなし）。Notebook/source削除なし。
7. Ending選択状態: 未選択。
8. Ending処理結果: `SKIPPED (not configured)`。RAWを直接HLS入力として使用。
9. HLS: 実FFmpeg変換・playlist/segment検証・ffprobe H.264/AAC検証PASS。既存`-hls_time 6`維持（実segment長は既存keyframe境界に従う）。
10. ZIP: 20,372,515 bytes、56 entries、全entry ZIP_STORED、`testzip()`エラーなし。既存上書き禁止publishを通過。
11. COMPLETED: HT075のcopy JSONがCOMPLETED、Errorなし。最終更新21:25:27.856439。完了済みHT075だけを別copyへ保存し、実GUIのStartを再実行した結果COMPLETED_SKIP。Notebook操作adapter呼出0、Preset/Generate0、RAW/ZIP/TXTハッシュ変更0。共通Pre-flightのhome navigationは1（対象Notebook openではない）。
12. TXT move: COMPLETED保存後にcopy側`input`から同copy側`raw_files`へ移動しMOVEDを保存。ユーザーの元TXTは移動していない。
13. Runtime: 実GUIで現在Job HT075、工程「動画完成を確認」、判断「artifact ready（動画完成）」、次処理「Download開始」を確認。RAW保存→Endingスキップ→HLS変換→ZIP作成→COMPLETEDまで構造化通知と画面captureを保存。GUIデザイン変更なし。
14. Modal: 告知modal自然出現なし。既存guardを通るが実dismissの成功を新たに主張しない。`ANNOUNCEMENT_MODAL_LIVE_AUTO_DISMISS: UNVERIFIED_LIVE`を継続。強制再現なし。
15. Quota: 今回は既存生成物の確認・回収のみ。新しいQuota不足返信なし。current reply→reservationの新規live証拠なし。意図的なクレジット消費なし。
16. Stop回帰: HT075完了のjob.next通知でowner worker上からStopを要求し、次job dequeue前に停止。navigation count 5→5、追加Notebook/download開始0、owned child process0、browser context終了。既存Stop関連テストも全件PASS。
17. tests: `pytest -q --basetemp=work/v122-recovery-full-01` = **443 passed in 270.20s**。従来441維持＋2追加。targeted 62 passed。`compileall -q src tests` PASS、`git diff --check` PASS（既存CRLF警告のみ）。
18. 本番JSON変更数: Ver1.1本番177 JSON **0**、現在Ver1.2.1側103 JSON **0**。試験前後のファイル別SHA-256辞書が完全一致。migration-backupsは集計対象外。state保存はwork/copyだけ。
19. 未解決事項: 今回の必須回収Gateに未解決なし。告知modal liveとQuota reservation liveは自然発生なし。既知の`SOURCE_UPLOAD_ERROR_LIVE_RECOVERY: UNVERIFIED_LIVE`は未変更。TAX120は生成待機checkpointを保持。新EXE/portableはまだ作成していない。

## Due確認の実測

| Job | 直前の保存期限 | 実確認開始 | 結果 | 新しい保存期限 |
| --- | --- | --- | --- | --- |
| TAX120 | 21:18:34.414274 | 21:20:30.358423 | GENERATING | 21:22:30.258895 |
| HT075 | 21:20:50.416874 | 21:20:50.625405 | GENERATING | 21:22:50.513429 |
| TAX120 | 21:22:30.258895 | 21:22:30.605646 | GENERATING | 21:24:30.519294 |
| HT075 | 21:22:50.513429 | 21:22:50.650542 | READY→即Download | 完了のため再poll対象外 |

TAX120初回期限は試験開始前に到達済み。期限を短縮・初期化していない。未完成→再待機、期限前open0、期限到達時再確認、READY時同job完走を実remoteで確認。追加テストでも注入clockで境界を再現。

## 変更の範囲

- `pipeline.py`: 待機jobの実artifact状態・最終確認時刻の保存、READY/RAW/Ending工程通知。
- `runtime_operation.py`: READY・RAW・Ending・HLS・ZIP用表示label。
- `hls.py`: 実変換直前と実ZIP作成直前の工程通知のみ。FFmpeg command・validation・ZIP方式変更なし。
- `tests/test_v122_recovery_continue.py`: due/reschedule/ready即回収/optional Ending/完成TXT移動/再Start保護/通知順の2tests。
- `DEVELOPMENT_RULES.md`: 今回のcopy限定live回収とGate後artifact削除の許可範囲を記録。
- 先行の逐次処理実装差分は保持。元3repo変更なし。

## 保持した成果物と証跡

実回収root: `C:\xampp\htdocs\PHP\DJDmaker\work\v122 recovery live dhnr0j8b`

- RAW: `raw_files\HT075_心が楽になるシリーズ_第75話_言葉が自分の心にも影響する.mp4`
- ZIP: `output\HT075_心が楽になるシリーズ_第75話_言葉が自分の心にも影響する.zip`
- TXT: `raw_files\HT075_心が楽になるシリーズ_第75話_言葉が自分の心にも影響する.txt`
- copy checkpoint: `system\jobs\481465e9f15e42c2b49d0d7a326cce53.json`
- report: `report.json`（runtime順序、inspection実時刻、Gate、ハッシュ照合、Stop診断）
- captures: `artifact.ready.png` / `raw.saved.png` / `ending.skip.png` / `hls.start.png` / `zip.start.png`。`job.next.png`は最初の待機job通知のcaptureであり完了画面ではない。
- 完了済み再Start: `work\v122 completed restart brvescm_\report.json` / `completed-restart.png`
- 再現harness: `work\v122_recovery_live.py` / `work\v122_completed_restart.py`（Git ignored）

RAW SHA-256: `B584604BAC7DB0E0A3DBF9DD4018DF4DCFBC6F35A7B639261A33F2C754280587`

ZIP SHA-256: `C4028868A74D56E09CBDCCE8189E88C1521C70C173C6F19D9C32A276A1725748`

**運用引継ぎ注意:** HT075のremote動画artifactは安全Gate後に削除済み。成果物と完了stateは上記copy側だけにあり、本番JSONへ反映していない。本番側の古い失敗stateから再生成・再回収するのではなく、このRAW/ZIP/copy checkpointを保持して引き継ぐこと。ユーザー原本への自動統合は今回実施しない。

DJDMAKER_V122_SEQUENTIAL_RECOVERY_LIVE_RESULT: PASS
