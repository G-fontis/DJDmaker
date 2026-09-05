# Unit 4 Multi-job Soak Acceptance

指示ID: `DJD-CHAPPY-V01-UNIT4-MULTIJOB-SOAK-ACCEPTANCE-001`
実施日: 2026-09-06（Asia/Tokyo）

## 1–5. Repository / Git

1. Repository: `C:\xampp\htdocs\PHP\DJDmaker`
2. Branch: `main`
3. HEAD before: `90bb25b56e2a03df85c85ef21f2539f842c8cff5`。HEAD afterはUnit 4ローカルcommitを指す。
4. Unit 4の実装・テスト・受入証跡をローカルcommitする。GitHub pushは実施しない。
5. runtime、build、distを除くUnit 4差分だけをcommit対象とし、commit後のtracked worktreeをcleanにする。

## 6–13. Live 3-job Notebook acceptance

6. Live job数: 3。`djd-multi-001`、`djd-multi-002`、`djd-multi-003`。
7. 使用TXT: `DJD_MULTI_001.txt`、`DJD_MULTI_002.txt`、`DJD_MULTI_003.txt`。
8. Notebook対応:
   - `djd-multi-001` / `DJD_MULTI_001` / `87e7ee06-0857-4b27-873a-29a105846d4a`
   - `djd-multi-002` / `DJD_MULTI_002` / `02b90185-99c1-47fa-8954-2a9a89d695a6`
   - `djd-multi-003` / `DJD_MULTI_003` / `0a2f5b48-1308-4db7-9efa-032b2ec08f16`
9. Completion監視: production既定値の初回600秒、以後120秒を変更せず実時間で観測。GNB_Creatorの判定は `artifact-library-item` 内の完全一致「再生」buttonであり、DJDmakerもこれをREADYの一次判定として踏襲する。再navigation後は2秒間隔、最大60秒で遅延DOMを待つ。
10. Download: 3件とも各job ID配下の一時downloadを経由し、対応するMP4を回収。Chrome CDP download、isolated directory、一時拡張子終了、検証後publishを使用した。
11. Artifact削除: 3件とも12項目gate合格後に、対象video card内の「その他」→Download markerを持つartifact menu→「削除」だけを操作した。Angular Materialが外側と内側の同一dialogを二重にrole公開する現行DOMは、`aria-modal=true`の内側dialogへ限定した。
12. Refresh結果: 全3 Notebookで削除直後、安定待機後、refresh後のartifact件数がそれぞれ `(0, 0, 0)`。復活0。
13. Notebook/source残存: 全3件でNotebook URL、Notebook title、対応するsource TXT表示を再読取しPASS。Notebook本体とsourceは削除していない。

## 14–18. RAW / Ending / HLS / ZIP

14. RAW一覧:

| RAW | size | duration | codecs | SHA-256 |
|---|---:|---:|---|---|
| `DJD_MULTI_001.mp4` | 1,796,827 | 69.868844秒 | H.264/AAC | `2EECED365157C6CF38D025D7B451EFEFD456D560582D0203590682B1210D6355` |
| `DJD_MULTI_002.mp4` | 4,048,844 | 64.226395秒 | H.264/AAC | `22BB6DF22CC8E2C11603235859194271B26BFCCB3ED0CBEE42AD6DB88B2D3E5A` |
| `DJD_MULTI_003.mp4` | 1,549,854 | 54.682993秒 | H.264/AAC | `29B1F14F00DD388CED6B1EFDC885098BE4BDAAB4080673DE0706CCFD155C5B3C` |

15. RAW hash不変: 全job完了後に再度pipeline cycleを実行し、3件すべてsize、mtime、SHA-256が不変。
16. Ending: 全3件`PASS`。最終音声逆探索、+0.5秒、Ending結合、ffmpeg/ffprobe検証を既存adapterで実行。
17. HLS: 全3件`PASS`。6秒segment、playlist/segment検証を実行。
18. ZIP: `DJD_MULTI_001.zip`（3 segments）、`002.zip`（8）、`003.zip`（2）。全件playlistあり、`testzip() is None`、全entry `ZIP_STORED`。

## 19–28. Multi-job / recovery / GUI / logs

19. Pipeline並列状態: Notebook laneは直列、media laneは設定値1/2のbounded worker。実FFmpeg同時実行テストでは設定2でpeak 2を確認。Notebook待機中に別jobのEnding/HLS/ZIPが進むことをcontrolled testとlive runで確認。
20. 取り違え: job IDごとのwork directory、Notebook ID/URL、stem、RAW、ZIPの対応を検証し0件。
21. Collision: 同一stemは処理開始前に後続jobを`OUTPUT_NAME_COLLISION`で停止し、既存RAW/ZIPを変更しない。別jobの既存ZIP誤採用も拒否。
22. Failure isolation: controlled 3-job testで1件を意図的に失敗させ、残り2件が`COMPLETED`になることを確認。
23. Pause/resume: 新規開始停止、進行中状態保持、deadline継続、Notebook/download/RAWの重複なしを確認。
24. Restart: `WAITING_VIDEO`、`RAW_READY`、`HLS_ENCODING`から復帰し、`COMPLETED`を再処理しない。live中に判明したdownload後context終了は、GNB_Creatorと同じ専用profileのBrowserManager再起動で復旧。JOB 1は保存済みNotebook identityの監視から再開し、再submit・再生成していない。
25. Scheduler: job別deadline、初回600秒、以後120秒、late poll、duplicate pollなし、全terminal後poll停止を確認。
26. GUI: offscreen実GUIで4行（Unit 3を含む）、全Job 4、処理中0、Notebook完了4/4、ZIP完了4/4、Error 0。
27. Filesystem集計: `raw_files/*.mp4` 4件、`output/*.zip` 4件でGUIと一致。
28. Logs: state logへstable `job_id`、`script_name`、message先頭`[stem]`を記録する回帰テストを追加。credential、cookie、tokenは記録しない。

## 29–31. Portable build

29. Portable build: `dist\DJDmaker_v0.1\DJDmaker.exe`、3,754,112 bytes、SHA-256 `F56F9391098606DA4C913438D7FFBEDD396C8FEA9D94AC20B62C4B74A3C07A19`。配布tree 408 files / 701,220,458 bytes。
30. Portable runtime: 日本語と空白を含むpathへcopyし、GUI title、safe shutdown、PySide6 plugins、FFmpeg/ffprobe 9.0.1、packaged Playwrightからinstalled Chrome、専用profile、settings write→別process readをPASS。
31. Portable Fake E2E: packaged codeとbundled FFmpegでFake Notebook→実MP4 fixture→RAW→Ending→HLS→ZIP→`COMPLETED`→delete gateをPASS。

## 32–39. Final audit

32. Tests: `180 passed in 32.00s`。`git diff --check` PASS。
33. DB: production codeにDB依存なし。job/settingsはversioned JSON。
34. Secrets: tracked sourceのAPI key、private key、token、password形式scanで検出0。browser profileはGit除外。
35. `.gitignore`: browser、input runtime、RAW MP4、output ZIP、system job JSON、work、build、distを`git check-ignore`で確認。tracked runtimeは各directoryの`.gitkeep`のみ。
36. 元repo変更0: AutoGeminiNoteBookCreator `28bd51d...`、GeminiNotebookVideoMerge `242a8dc...`、FukuzemiApp `434e70c...`はいずれもclean。
37. 未解決事項: Unit 4の機能受入を妨げる項目なし。正式release向けのcode signing、正式ZIP/checksum、第三者clean Windows実機、FFmpeg license/codec構成の最終reviewはrelease工程へ残す。
38. Release readiness: unsigned内部候補としてPASS。約701MBのため軽量化は別途判断。正式配布物ではない。
39. 次Unit推奨: 正式release hardening（署名、第三者clean-PC、license確定、正式ZIP/checksum）を実施する。

`UNIT4_RESULT: PASS`
