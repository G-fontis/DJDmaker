# Ver1.2.2候補：逐次再開・現在工程表示（未release）

返答ID: DJD-CODEX-20260907-V122-SEQUENTIAL-PROGRESS-001

今回指示ID: DJD-CHAPPY-V121-SEQUENTIAL-RESUME-RUNTIME-VISIBILITY-MODAL-FULL-001

対象: `C:\xampp\htdocs\PHP\DJDmaker` / `main`

## 最新live追記（2026-09-07・今回の追加承認範囲）

返答ID: DJD-CODEX-20260907-V122-LIVE-COPY-002

**今回の限定live送信Acceptance: PASS**。ユーザーの追加承認に基づき、本番の既存失敗Notebookを1件ずつ、合計2件使用した。state保存はコピー側のみ。Download/削除・完成待機は今回の試験対象にしない。これを配布版release全体のPASSとは混同しない。

|順番|対象|実DOMの開始前判定|今回の操作・返信|コピー側の保存|
|---|---|---|---|---|
|1|TAX120 / `3d5f3ef52536445a8093dcc9877de108`|NOT_STARTED / Source READY / Preset SENT / 旧返信 QUOTA_EXHAUSTED|Preset再送1回→今回user message確認→今回返信 GENERATION_ACCEPTED|WAITING_VIDEO、next_poll_at保存|
|2|HT075 / `481465e9f15e42c2b49d0d7a326cce53`|NOT_STARTED / Source READY / Preset NOT_SENT / NO_RESPONSE|Preset送信1回→今回user message確認→今回返信 GENERATION_ACCEPTED|WAITING_VIDEO、next_poll_at保存|

1件目は過去にPreset送信済みだったため、未送信経路を確認する目的でのみ2件目を使用。3件目以降は開いていない。

- 1件目: script開始からsend有効確認74.505秒、今回user message確認74.724秒、生成開始返信分類91.102秒。
- 2件目: script開始からsend有効確認75.677秒、今回user message確認75.874秒、生成開始返信分類96.345秒。
- 上記はrunner起動基準であり、厳密なStartボタンクリック起点とは約0.2秒の開始予約分等が異なる。全件巡回を待たず、当該jobの診断→送信→返信分類→永続保存→nextを実行。
- Source再upload 0、Notebook作成0、Download 0、artifact/Notebook/source削除0。試験の禁止操作guard呼出し0。
- 2件ともPre-flight 7/7 PASS。既存専用profileのwarm sessionを使用し、Fresh login成功とは記録しない。
- runtimeの現在job/Notebook/phase/stage/decision/next action/attempt/outcome/countを実GUIのsignalと表示から確認。送信時は`chat.send`、判断`SOURCE_ALREADY_READY`、次処理`中央ChatへPreset送信`、attempt 1/3。返信待機と保存後nextへの変化も記録。
- 元177 JSONおよび現行Ver1.2.1の103 JSONは、両試験それぞれの前後でSHA-256すべて一致。state保存先は下記の隔離rootのみ。
- 今回の新しい返信は両件とも生成開始。現在Quota不足→予約のlive分岐は発生していないため、実予約成功とは記録しない。既存fixtureの担保を維持する。
- modal出現0。`ANNOUNCEMENT_MODAL_LIVE_AUTO_DISMISS: UNVERIFIED_LIVE`を継続。強制再現0。
- 生成開始返信確認後に試験GUI/browserを終了。remote動画の完成自体とDownloadは未確認で、そのまま残した。原本stateは変更していないので、後続の通常再開診断でremote状態を再取得して回収する。
- 1件目の試験runner終了監視に誤った`BrowserManager.page`参照がありAttributeErrorを出した。製品sourceの例外ではない。対象試験GUIを通常のWM_CLOSEで終了し、copy stateと前後hashを保存できた。runnerを訂正した2件目は自動終了まで完了。両件とも製品operation error callback 0、browser閉鎖を確認。
- 製品sourceは前回全441 tests PASS時点から追加変更なし。今回変更は試験runnerと記録のみ。Version変更/build/commit/pushは行っていない。

証跡（private runtime、Git対象外）:

- `work/v122 live copy shy03c8d/report.json` / `runtime.png`（TAX120）
- `work/v122 live copy xbyv9qzy/report.json` / `runtime.png`（HT075）
- 各rootの`system/jobs/<job_id>.json`にstate、preset snapshot、次回確認時刻を保存。

V122_LIVE_PRESET_QUOTA_COPY_ACCEPTANCE: PASS

## 前回判定・53項目履歴（上の最新live追記を優先）

**BLOCKED（live Acceptance未完了）**。source変更とfixture検証を進めた段階。正式VersionはVer1.2.1のまま。build / commit / push未実施。配布済みEXEは変更していない。

既存の独立試験用`PRESET_CRITICAL_B`を通常のBrowserManagerで読み取り確認したが、Notebook URLを開いた後に`https://notebook.google.com/`へ戻り、中央Chatが存在しなくなった。`WRONG_INPUT_TARGET`を記録。ホームへ戻る原因は不明であり、selector不具合やNotebook削除と断定しない。送信・upload・生成・削除は0。認証profileリセットなどの強制再現も0。

本番側にはDownload待ちの完成artifactもあるが、コピー側で削除すると、本番JSONを書き換えない条件の下では本番の回収ができなくなる。このため勝手にlive削除対象へ採用していない。4種類のlive試験用Notebook指定をユーザーへ依頼中。

## 53項目

1. HEAD before: `7e5d8ca900926320f50a5ad4dcb218a73ba36b50`。
2. 旧構造: controllerが`resume_failed_jobs()`を全件実行し、終了後`run_cycle()`へ進んでいた。
3. 原因: 再開診断と実処理が別の巡回だった。保存は進むが最初の送信/回収が全診断完了まで始まらなかった。
4. 新queue: Startではローカルbookkeepingのみ初期化。各jobのcheck→actを同一queueで処理。全件remote診断APIは旧試験/明示診断互換として残すが通常Startからは呼ばない。
5. 実装: 単一IDの再開診断後、同じjobを即実行し、outcome保存後に次へ進む。1job指定時は他jobのJSON一覧診断もしない。
6. source未完了: 元の`ensure_source`を維持し、missing upload / ready gateを同job内で処理。実adapterを使うfixtureで次jobより前のupload→ready→sendを確認。
7. Preset未送信: snapshotを変更せず、元の中央Chat送信とreadback、今回返信correlationを使用。
8. 古いQuota: 古い返信は原因分類にのみ使用。生成未開始なら同jobの今回送信を行い、その返信により生成/予約を判断。fixture確認、live未確認。
9. generating: `WAITING_VIDEO`と永続deadlineを保存し、長時間そのjobを占有しない。
10. ready: 診断直後Download/RAW検証/削除/Ending任意/HLS/ZIP/COMPLETED/TXT移動。同jobの完了保存が次job確認より前になるfixtureを確認。
11. reservation: reset/次回確認期限前はremoteを開かない。期限到達後は既存artifact回収確認のみ。
12. next_check: 既存`next_poll_at`を再利用。scheduler clockでclaimを保存してから観測。待機理由は重複表示・保存を抑止。
13. COMPLETED open: fixture 0。完了TXT補完はローカルのみ。
14. navigation: 100件の模擬remote fixtureで旧200→新100。実Google上100件の計測ではない。
15. first action: 1件診断33秒と仮定した模擬時計で旧3300秒→新33秒。実機時間ではない。完成artifactのDownloadは次jobのcheckより前になることをevent順序で確認。実機Download開始時間は未計測。
16. Runtime job: 現在処理中のID/台本名を表示。
17. Notebook: 対象URLを表示。元データやprompt本文はruntime eventに含めない。
18. phase: 開始前確認/再開処理/逐次処理/回収処理/保存済みRAW変換。
19. stage: Notebook open、source、Chat、返信、完成確認、Download、media、modal、Stopの工程通知。
20. decision: 判定と明示skip reasonの日本語表示。
21. next action: 次工程を表示。
22. message: 時刻付きの読み取り専用欄、最大300行。既存ログ画面も維持。
23. progress: 処理済み/対象件数、attempt、elapsed（GUI timerで更新）。
24. skip: COMPLETED、fatal、期限待ち、予約待ち、明示再試行待ち、不明状態を表示。新しいruntime_outcome/runtime_reasonをjob JSONへ保存。
25. no-op: 呼出し後に進捗がなく明示待機でもない場合、NO_OP_JOB_TRANSITIONとして記録。
26. fail-fast: 不明診断/無処理が3件連続なら例外をrun全体へ伝播して停止。次jobへ進まないfixture。
27. modal条件: AUTH直後に出やすいという仮説は未検証。強制再現なし。
28. classifier: 既存ANNOUNCEMENT/ONBOARDING/SAFE_INFO許可、ACTION_CONFIRMATION/UNKNOWN停止を維持。
29. dismiss: `_open_job`の既存guardを維持し、runtimeへ検出/閉じる/復帰を通知。artifact削除工程のBlockingModalErrorも飲み込まず停止。
30. scrim: dialog非表示、scrim非表示、対象control trial、存在するChat/source controlsのtrialを確認。未知modalは閉じない。
31. live modal: `ANNOUNCEMENT_MODAL_LIVE_AUTO_DISMISS: UNVERIFIED_LIVE`。今回liveで案内modalの実dismissを確認していない。
32. Stop during modal: 既存cancel fixtureとmodal停止伝播の追加fixtureで確認。今回の実Google再計測は未実施。
33. Stop: dequeue/remote操作の既存checkpointを維持。次job 0の追加fixture。Stop要求/終了待ち/停止完了を表示。
34. Close: 既存owner-thread cleanup/Windows Job Object方式を変更しない。Windows上のsource GUI描画・終了を確認。新portable未build。
35. Ending: 未選択でRAW→HLS/ZIP維持。保存済みRAWのFFmpeg 1/2並列も維持（Notebook操作は直列）。
36. Preset未送信live: 未完了。以前の試験用Notebookでホームへ戻る現象を確認し、安全に送信前停止。
37. Quota live: 未完了。専用対象の指定待ち。
38. artifact ready live: 未完了。本番の完成物は勝手に削除しない。
39. generating live: 未完了。専用対象の指定待ち。
40. tests: **最終source全441件PASS（205.86秒）**。既存403件＋逐次処理/表示/安全回帰38件。`.venv\Scripts\python.exe -m pytest -q --basetemp=work/v122-full-04`。compileall / diff --check PASS。途中の440件PASS後に追加したfatal分類の移行保護・並列件数表示もこの全件実行に含む。
41. performance: 模擬100件で二重巡回削減確認。Google実時間・実navigation計測は未完了。
42. copy data: 175件の隔離JSON試験PASS。75 COMPLETEDを保持、100件がWAITING_VIDEO。check100 / submit100、先頭event列はcheck→submit→check→submit。input/raw/output pathを隔離rootへ変更し、本番TXT移動を防止。`work/v122-copy-audit.json`に結果保存。これはFake remote試験でありliveではない。
43. 本番JSON: この作業での書込0。旧175jobs+settings/presets2の177件は、copy試験前後・既存backupのSHAがすべて一致。現Ver1.2.1側は調査時100jobであり、177という過去件数と混同しない。
44. Version: Ver1.2.1維持。Ver1.2.2へのbumpは未承認Gate完了後。
45. build: 未実施（source/live全PASS前禁止）。
46. portable: 既存Ver1.2.1は無変更。新候補なし。
47. EXE smoke: 未実施。source GUI画像は`work/v122-runtime-gui.png`（Windows Qt）。
48. commit: 未実施。
49. push: 未実施。
50. HEAD/origin: 両方`7e5d8ca900926320f50a5ad4dcb218a73ba36b50`（ローカルtracking ref照合）。
51. ahead/behind: 0/0。ただし今回source/test/docは未commit差分あり、Git cleanではない。
52. phase2: `46c4599e76383b663552aec3f052e0dca8cc09f9`のまま。phase2変更/phase2-v12/phase3作成0、元3repo書込0。
53. 未解決: 4種類live・実時間計測・build後smokeが未完了。`SOURCE_UPLOAD_ERROR_LIVE_RECOVERY: UNVERIFIED_LIVE`も維持。source fixtureをlive PASSへ読み替えない。

## 追加安全修正

- 同じrunで処理失敗したローカルmedia jobを、その直後の再開queueへ再投入しない。
- 既存ZIPとの衝突はfatal分類を保持し、次Startでも無条件に既存ZIPを採用しない。
- outcome保存で完了TXT移動のarchived path/hash/statusを巻き戻さない。
- schema migrationでも既知のfatal分類をretryableへ巻き戻さない。

## 前回停止時点

ローカルsource/test検証は完了。既存177 JSONのSHA比較と175件Fakeコピー試験もPASS。liveの4パターンを満たす専用Notebookの指定待ちであり、Ver1.2.2へのbump、build、commit、pushは行わない。実行した読み取り専用browser probeは終了済み。

DJDMAKER_V122_SEQUENTIAL_RESUME_RUNTIME_MODAL_RESULT: BLOCKED
