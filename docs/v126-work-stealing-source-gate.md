# Ver1.2.6 source / live gate

最新の製品Gate・最終成果物SHAは [Ver1.2.6 Release Gate](v126-final-release.md) を参照。以下は各時点の経過記録であり、旧BLOCKED/未実施/旧候補SHAを最終判定に使用しない。最終全件607 PASS、EXE回収・603.844秒待機・保存隔離・終了・GUI復元までPASS。Git最終結果は `work/v126-final-release-result.txt` へ保存する。

## Release準備状況（最新記録を優先）

- 追加返信トリガーまで含む全件回帰 `work/v126-final-gate-004.xml`: 607 passed、283.33秒。
- 配布verifierを拡張: 実Chromeローカル上限DOM fixture、local完了後のartifact確認/Download、共有Preset、両GUI再開buttonなし、Pause→Start再開→Stopを確認。`work/v126-source-limit-fixture-report-001.json` PASS。10分実時間待機はEXEで追加検証する。
- source/live Gate後に版数を1.2.6へ変更。初回metadata変更時にGUIタイトル/テスト期待値の1.2.5残存を横断検索で発見し、自分が開始したbuild/testのみを停止して訂正した。停止buildのexit -1はポリシー拒否ではない。再buildは既存PyInstaller onedir正式scriptでexit 0、ExecutionPolicy RemoteSignedのまま。
- 正式候補 `dist/DJDmaker_Ver1.2.6/`、ZIP/展開全408ファイルhash一致、private profile/runtime media混入0。EXE 3,991,129 bytes、SHA `31F7CF3B1C8E7B0D5A515DC821141F079DE5D29E5F1407913C216BBA559DDCA4`。ZIP 277,777,831 bytes、SHA `9A48727E23B9FB15C056146E7E918E38D0C65B2BFB3E133E555A1E9B748A6856`。
- Version変更とverifier拡張後の全件回帰、展開EXE・10分待機・保存隔離・shutdownのGateは実行中。commit/pushはまだ行わない。以下の過去の594/605件・live未実施等の記録は各時点の履歴であり、最新完了報告ではない。

## 続行確認：Local終了後も巡回（2026-09-08）

- `work/v126-local-then-collect-001/result.json`: 実GUI/controller・保持中の現在返信クォータgateを使用。試験RAWコピー1件をローカル再処理してCOMPLETEDにした後、生成中REV024を実Notebookで確認し、未完成なので次期限へ保存、scheduler.waitへ移行。local終了だけによる自動停止なし。試験がwait境界で協調Stopした。
- stateは新しいcopy runtimeのみ。本番system JSON変更0、入力元TXT・試験元RAW hash不変。新規生成・Preset再送なし。
- 最新source両GUI再検証: `work/v126 GUI 日本語/source-copy-005/source-gui-result.json` PASS。Preset共通CRUD/同一session選択、切替後jobとscheduler再表示、起動時未選択、両GUI再開buttonなし。Phase2画像を目視確認。EXE検証とは区別。
- ユーザーの追加確定に従い、REV105の今回生成分クォータ不足→後回し、REV115のクォータ中実Download、600秒実wait再探索をsource/live Gate証跡として採用する。AI上限banner・Chat無効化は未観測のまま記録し、観測済みと偽らない。

## 今回生成分のクォータ不足も後回しトリガー（ユーザー追加確定）

- 実上限bannerやChat無効化がなくても、現在の生成依頼への新しい返信が必要クォータ不足ならCloud生成を停止する。既存current reply→QUOTA_EXHAUSTED→CloudLimitReached→Local/回収への遷移を維持。REV105のlive結果がこの条件に該当する。
- runtimeのquota.detectedにdecision/next_action/messageを明示し、直前のalmost警告の「生成継続」が残らないよう訂正した。過去返信・almostのみはトリガーにしない。回収可否は引き続きクォータと独立。
- 指定文言そのものを用いて、解除時刻あり/なし、hard bannerなし、送信1回、Cloud停止、回収・local有効を回帰追加。関連28 tests PASS（8.81秒）、compileall/diff --check PASS。追加後の全件回帰とEXE buildは未実施（直前全件605 PASSとは区別）。commit/pushなし。

## クォータと独立した完成確認・回収のlive結果（2026-09-08 13:20 JST）

- ユーザー確定: 完成確認・Download可否はクォータ条件と無関係。`Capabilities.from_limit(True)`でもcan_check_artifact/can_download/can_local_processはTrue。開発ルールの旧「reset前はremote操作を行わない」を訂正した。今回本体コードの追加変更なし。
- `work/v126-batch-live-001/result.json`: 本物のGUI/controllerで600秒待機後に第2cycleを実行。REV024は未完成なので再待機、REV115は13:19:44 JST再確認、artifact ready→同一jobでDownload開始→RAW保存→Ending未設定skip→HLS→ZIP→13:20:06 JST COMPLETED。試験コピーTXT移動もMOVED。
- 回収中もcloud_limit.active=True。新規Preset送信はbatch合計2回のままで上限検出後0、Download成功1回。RAW 12項目gateすべてTrue、2,127,323 bytes、356.821043秒。ZIP 841,772 bytes、HLS結果PASS。
- RAW SHA-256: `541A48FCF42274F78266EA4C5700717EB0E0E01E05E1F1B4BC345D902A28E9C9`。
- ZIP SHA-256: `9D77BD0F1C133FE0609E3C5EFE89BDC64F5B26C87D64C7BDEE0DA5B378E46A2F`。
- 成果物: `work/v126-batch-live-001/raw_files/REV115_国を変えるシリーズ_第115話_街頭演説.mp4`、`work/v126-batch-live-001/output/REV115_国を変えるシリーズ_第115話_街頭演説.zip`。remote artifactはDELETE_PENDINGとして保持（削除0）。Notebook/source削除なし。削除成功とは報告しない。
- 本番Ver1.2.4/1.2.5 system JSON前後変更0、元source変更0、errors 0。状態と成果物はcopy側のみ。元jobとのparent対応を保持し、本番への無確認再投入は禁止。
- 2回目のscheduler.waitで試験側から協調Stopした。アプリが未完了jobを自動終了したものではない。Stop後追加Notebook/送信/Downloadなし。最終scheduler表示もcollection/local=True、generation=False。
- 回収独立性を含むscheduler回帰15 passed（3.36秒）。直近全体回帰605 passedを維持、今回の変更は記録のみ。diff --check PASS。
- この結果は「動画生成クォータ不足のcurrent replyを受けた状態での回収PASS」。AI上限bannerによるChat無効化は今回未観測であり、同一視しない。EXE/build/release Gate未完了。version変更・build・commit・pushなし。
- 停止後の読み取り確認 `work/v126-quota-reply-check-001/result.json`: REV105の今回返信全文に「利用枠が回復した際（約3時間後）」を確認。16:09:38は13:09:38での返信分類時刻＋3時間、16:14:38はさらに安全余裕5分。Googleが確定時刻として表示した値ではなく返信の概算由来であり、almost bannerの16:21とは区別する。13:20:57時点もChat enabled/実上限bannerなし。追加送信・upload・削除0、本番JSON変更0。
- ZIP再検証: CRCエラーなし、全4entryがZIP_STORED、playlist.m3u8を含む。

## 実上限まで未生成リストを連続投入する追加指示

- 実行途中の観測: REV115は生成開始、次のREV105で今回replyが「動画生成に必要な利用枠（クォータ）が不足しているため、直接の動画生成を行うことができません」と表示。`chrome-limit.png` に実画面を保存。Chatは有効、bannerはalmostのままなので、「Chat無効化のlive PASS」とは区別する。
- 既存current-reply分類によりQUOTA_EXHAUSTEDとなり、copy側Cloud gateを閉じた。保存resetは16:09:38 JST、resumeは16:14:38 JST。bannerのalmost reset 16:21とは別の根拠（reply分類）であり、両時刻を同一と報告しない。返信全文・時刻の由来は最終確認対象。
- batchのPreset送信は2回で停止。その時点のlocal backlogは0、REV024/REV115は次回期限前のため開かず、実GUI/controllerが600秒waitへ入った。待機終了後の再探索・回収を引き続き確認中。

- ユーザーが「実上限が出ていない場合は未生成リストからどんどん生成」を明示承認。`work/v126_batch_live_gui.py` で実GUI・共通GuiPipelineController/PipelineCoordinatorを実行する。
- 試験root `work/v126-batch-live-001/`。本番WAITING・Notebook URLなし・RAWなし・TXT実在の21件を独立copy jobへ取り込み、生成済み試験REV024を追加した計22件。本番REV024を再投入しない。既存URLを持つREV149は新規投入から除外し、既存remote診断対象として別扱い。
- 元jobとの対応はcopy jobのparent_job_idおよび最終report mappingへ保存。Notebook URLはcopy側だけに保持する。生成開始後の本番移行にはこの対応を照合する必要があり、本番WAITINGを無確認で再Startしない。
- source/live Gate前のbuild/version bump/commit/push禁止を維持。実上限まで生成優先、次にLocal/期限到達artifact回収、未完なら600秒waitを実GUI/controllerで観測する。実上限を人工的に作るfault injectionはしない。

## 本番未生成リストからの試験対象選定

- 結果: **almost中の生成継続live PASS**。source upload 1 / chat.sent 1 / generation.accepted 1 / WAITING_VIDEO保存 / job.nextを確認。`after.png` でStudioの実動画生成中表示を目視確認。回答中の一時disabledでもlimit.warningを維持し、偽のCloud gate停止なし。実上限は未発生。
- 原本Ver1.2.4/1.2.5 system JSON・TAX087保護成果物の前後差分0、REV024元TXT不変。試験で作成したNotebook/生成動画はremoteに保持し、本番元jobにはURLを反映していない。今後の本番移行時にはこの対応を照合し、盲目的に二重生成しない。
- send-only helperの終了後、欠けていたcopy job監視日時を既存PersistentPollSchedulerで補完。観測時刻13:04:18 JSTを起点に次回13:14:18 JST。`work/v126_schedule_pending_copy.py`。本番jobへは書き込まない。
- 605 tests PASSのsourceを使用。実上限時Local→Download→10分再探索、EXE/build/release Gateは未完了。正式全体判定は引き続きBLOCKED。新規試験Notebookで生成できたことは、TAX087の不一致原因の確定を意味しない。

- ユーザー追加指定により本番WAITING listからREV024 (`d862ad201db94017ba37f75c83d89ad6`) を選択。元jobはNotebook URLなし・RAWなし・TXT実在。独立test job `04e4c7bd71854e01b944b524a8b1db69` を作り、本番JSONを更新せずに試験する。Presetは直前試験と同じ承認済み日本語・ペーパークラフト本文。
- 新規試験Notebook: `https://notebook.google.com/notebook/b27d0f65-ce2b-4c74-9944-0905f20c34b0`。sourceアップロード・Studio準備完了後の送信を検証中。証跡保存先 `work/v126-pending-live-001/`。
- ユーザー指摘「別Notebookに同タイトル・同sourceの完成動画があるため、TAX087が作成中と返すのでは」について: 現時点では裏付けなし。同一Notebook内の過去の作成開始replyが残っていることはDOM確認済みだが、これが原因とも確定しない。他Notebook参照・重複検出・Google内部状態はいずれも原因未確定として扱う。

## TAX087独立生成試験と回答中誤検出（2026-09-08、最新）

- 修正後全件回帰: **605 passed / 0 failed / 314.52秒** (`work/v126-full-gate-003.xml`)。compileall / diff --check PASS。誤検出修正後のsource Gate結果としてこの証跡を使用する。
- 13:00 JSTの最終読み取り再確認 (`work/v126-after-send-003/result.json`, `notice.png`) でもstable artifact NOT_STARTED、Studio出力なし、Chat enabled、almostのみ。今回replyは作成中と述べているが実生成開始は未確認。Preset追加送信0、本番JSON前後差分0。新しい試験Notebookを1件作成し同じTXT/Presetで検証する案をユーザーに確認中。
- `DJDMAKER_V126_WORK_STEALING_SCHEDULER_RESULT: BLOCKED`。605件のsource回帰PASSと実送信1回は確認済みだが、修正後liveの正常完走・実上限Gate・build以降は未完了。

- ユーザーが旧COMPLETEDとは独立した試験jobでの生成を明示承認。旧recordのPreset本文を使用し、移動済みTXTをtest_outputからcopy。元COMPLETED・RAW・ZIP・TXTを保持した。
- 初回helperはgeneration_presetの引数不足で送信前にPRESET_NOT_SELECTED。`work/v126-almost-send-live-001/result.json`。helperだけ修正して再試行。STOP後の診断screenshotもcancellation境界を修正。本体の不具合とは区別する。
- 実送信試験 `work/v126-almost-send-live-002/result.json`: test job `fcba9c8bf5f744c89c176f33e166b14f`。source READY / almost / Chat enabledからPriority1へ進み、chat.send=1 / chat.sent=1を記録。upload・新規Notebook・artifact削除は実行していない。Ver1.2.4/1.2.5 system JSONとTAX087元成果物の前後差分0。
- **本体の誤検出を発見**: 回答中placeholder `回答しています...` の一時disabledとalmost bannerを合成してCloudLimitReachedを発火し、copy側だけを16:26待機にしてしまった。このcloud-limit.jsonは偽陽性の証拠であり、実上限live証拠として無効。元ファイルは保存したまま、正式再開に流用しない。
- 修正: almost warningとdisabled属性だけではhard gateを閉じない。明示的なreached/Chat disabled noticeが併存すれば停止を維持。回答中の一時無効＋almostと明示hard併存の回帰テストを追加。これ以前の604 PASSは修正前証跡。
- `work/v126-after-send-002/result.json`: 再送0の読み取り確認。今回user messageと今回replyをDOM確認。replyは「すでに…作成を進めております」だが、既存engineの待機後artifactもNOT_STARTED。返信文だけで生成開始PASSとはしない。almost継続 / Chat enabled / hard_limit null。本番JSON差分0。JSON保存後のconsole表示で絵文字のcp932 encoding errorが発生したが、証跡JSON保存とbrowser終了は完了している。
- 現状: almost中の実送信1回は確認したが、回答中誤検出修正後のlive再検証・実artifact生成開始・実上限到達・Local→Download→10分再探索のGateは未完了。動画やquotaを強制消費する追加送信は行わない。Version/build/commit/pushは未実施。

## 指定Notebookの事前確認（2026-09-08 12:47 JST）

- ユーザー指定URL: `https://notebook.google.com/notebook/3870ed64-65f1-4612-a4c4-446238b9cde8`。
- TAX087。実画面でsource 1件・Studioカード有効・現在artifact NOT_STARTED・Chat enabled・almost警告を確認。`work/v126-user-notebook-001/result.json` / `notice.png`。送信・upload・削除0、本番Ver1.2.5 system JSON前後差分0。
- 現行Ver1.2.5 jobsには対応URLなし。旧Ver1.2.4 `system/migration-backups/resume-v2/jobs/2f30e4f8474540e0991f0bd82456e071.json` には同URLのCOMPLETEDとRAW/ZIP回収済み記録がある。現在artifactがないことを「一度も生成していない」とは読み替えない。
- COMPLETED保護と区別するため、旧job・成果物を保持した独立試験jobでの新規生成可否を確認中。旧jobのWAITING化・Preset送信は未実施。

## 2026-09-08 続行試験（返答ID DJD-CODEX-20260908-V126-CONTINUE-002）

- almost修正を含む全件回帰: **604 passed / 0 failed / 345.31秒**。`work/v126-full-gate-002.xml`。実行中のsource/test編集なし。以下の「修正後全件未実行」は過去時点の記録であり、この結果で更新する。

- 現行sourceの実DOM確認: `work/v126-notice-002/result.json`。12:39:55 JST、almost英語bannerを `limit.warning` として検出、hard_limit=null / chat_enabled=true。本番system JSON前後差分0。これは非停止判定のlive確認であり、生成依頼そのものの成功確認ではない。
- 既存失敗Notebookを最大2件だけ確認。REV018 (`45509dc549f74e18bcacde35d5816193`) はNOT_STARTED / source PROCESSING / preset NOT_SENT。画像ではsourceエラー表示とStudio無効を確認したため未準備として送信せず。sourceエラーをPROCESSINGと返す現在判定は記録するが、復旧成功とは扱わない。`work/v126-error-live-001/result.json`。
- REV121 (`33ba881d0bc24ae9be76ee2c6bb801b6`) は実artifact READY。コピー側のFAILEDを既存pipeline診断によりDOWNLOAD_PENDINGへ復元。本番JSONと実台本は不変、Preset再送・再生成・artifact削除なし。Error liveのcheckpoint復元を確認。`work/v126-error-live-002/result.json`。
- 両対象もalmost警告のみ。実上限到達は未観測。生成試験に使えるsource読込済み・動画未生成のNotebook URLをユーザーへ依頼した。2件を超える本番Notebookへの試験拡大やquotaの強制消費はしない。
- almost対応後のsource GUI smokeを再実行: `work/v126 GUI 日本語/source-copy-004/source-gui-result.json` PASS。両GUI切替・共通Preset CRUD・session選択・job表示・Scheduler再表示・再開buttonなし・再起動時未選択。これはoffscreen source試験でありEXE Gateとは区別する。
- compileall / diff --check PASS。元3repo Git status clean、phase2 ref `f6f5871925f44718f51a106f7f55fa44e8f882b1` 不変。main HEAD `f2dced0cb0dd88017dcccf7978f6a128677223eb` 不変。
- 正式Version 1.2.5、v126 build / cleanup / commit / pushは未実施。source/live全Gate前にreleaseへ進めない。
- 続行時点の最終判定: `DJDMAKER_V126_WORK_STEALING_SCHEDULER_RESULT: BLOCKED`。source回帰は通過したが、almost中の実送信・実上限中Local→Download→10分待機再探索のlive Gateは未完了。試験対象URLの回答待ち。既存2件の診断結果を根拠に、全Gate PASSやrelease完了とは報告しない。

## 2026-09-08 上限接近警告の訂正（以下の旧判定より優先）

- ユーザー確定: 実際の上限到達・Chat無効化まで生成を継続する。
- 実DOMには `You're almost at your AI usage limit. Limit resets at 16:21.` が表示されていた。旧検出器は日本語のみを探索し、nullを返していた。「上限表示なし」という報告は誤り。`work/v126-notice-001/result.json` はChat入力がenabledであることと、この英語bannerを記録している。
- source修正: 英語warningをruntimeへ通知するが生成停止例外は出さない。英語hard limitは既存の解除時刻＋5分gateへ渡す。空入力のSend disabledだけではChat disabledとしない。旧credit検出の汎用usage limit一致もalmost警告では枯渇扱いにしない。warningでjob checkpointや既存hard gateを変更しない。
- 関連回帰: `tests/test_v126_usage_warning.py` と `tests/test_v125_limit_pause_contract.py` は57 passed。既定Tempのアクセス拒否によるsetup error後、新規 `work/v126-warning-tests-001` を指定してPASS。全594件PASSはこの追加修正前の結果であり、修正後全件再実行・EXE反映・hard limit実機確認は未完了。
- 今回live: REV049の実Download→12項目RAW gate→Ending未選択skip→HLS/ZIP→copy job COMPLETED成功。証跡 `work/v126-live-collect-001/result.json`。本番参照を保護するためremote artifactは残した。hard limit中の回収を確認したものではない。
- 本番保護: 前回baselineとの差分57件は今回live開始前から存在していた。原因は未断定。今回各live前後のJSON差分は0件。古いbaselineとの比較を0件と報告しない。
- この補正ではbuild / version変更 / commit / push / 本番JSON更新は実施していない。Release Gateは未完了。

返答ID: DJD-CODEX-20260908-V126-SOURCE-GATE-001

今回指示ID: DJD-CHAPPY-V126-CURRENT-WINDOW-DUALGUI-PRESET-WORK-STEALING-SCHEDULER-FULL-001

対象: `C:\xampp\htdocs\PHP\DJDmaker` / `main`。
正式Versionは1.2.5のまま。以下のsource変更は未commit。
Sourceとliveが揃うまでVersion変更、portable build、release commit、pushをしない。

## 確認した原因と変更

Scheduler旧実装は、使用量上限中の `run_cycle()` でローカル処理後にreturnしていた。
この分岐が期限到達artifact確認を除外していた。またcontrollerはFAILEDや保存保留を含む
状態でrunを終了し得た。新実装は生成投入、ローカル処理、期限到達回収、Error再診断、
600秒の割込み可能待機の順で探索し、保存保留や将来期限のjobが残る間は完了扱いにしない。

Capabilityのglobal値は操作の許可範囲であり、Google UIで成功した証拠ではない。
実際のartifact確認・Downloadは既存adapterが現在DOMで実行する。
操作失敗時は当該jobの `WAITING_REMOTE_ACCESS`、次回期限、回数を保存し、他jobを継続する。
既存RAW検証、安全gate、Ending任意、出力の衝突保護は維持する。

Browserについては `browser.py` の `auth_process_alive` が現在のPopen.poll()、
automation側が現在contextの接続状態を使用し、Start前にprofile lockを確認している。
履歴から現在open/closedを推測するproduction分岐は検索範囲で見つからなかった。
したがって「旧履歴分岐を発見・削除した」とは報告しない。起動方式は変更していない。
`navigation_result` 等は診断用であり、開始可否の根拠ではない。
AST contractで診断属性を条件分岐へ持ち込まないこと、過去open/closed文字列に依存せず
現在process終了状態が反映されることを検証する。

検索: `history`, `closed`, `closed_at`, `chrome_closed`, `browser_closed`,
`auth_closed`, `last_closed`, `was_closed`, `close_history`, `previous_session`, `previous_auth`。
productionのclose関連一致は現行preflightの `auth_chrome_closed` など。
`chat_flow.py` / `notebook.py` のChat history保護と、shutdown診断は別目的なので削除しない。

PresetはもともとSettingsDialogとMainWindowが同じRepositoryを受け取る構成だった。
独立したPhase2用DBやJSONが存在した証拠はない。一方、GUI再構築時に最新Runtimeを
再適用しないこと、メインGUI切替時にPreset一覧の共通読込経路がないことは確認した。
両GUIの共通部へ選択欄を置き、SettingsDialogも同じ `PresetViewModel.load()` を使用する。
既存RepositoryのCRUD/検証serviceを共用し、別キャッシュ・別JSONを作らない。
同じsessionの選択を保持し、新しいRepositoryで起動した場合の選択は空にする。
元のユーザー環境でのPreset不具合の因果関係すべてを確定したものではなく、live照合は残る。

## テスト証跡

本番保護baseline: `work/v126-source-baseline.json`。
最終Source固定hash: `work/v126-final-source-baseline.json`。
回収専用ボタンの修正前は593 passed（`work/v126-source-final-tests.xml`）。
その修正を含む最終全件テスト: `work/v126-source-final-594-tests.xml`。
結果: **594 passed / 0 failed / 304.01秒**。全件実行中のsource/test変更0。
`compileall -q src tests` と `git diff --check` もPASS。
追加contract: `tests/test_v126_scheduler_contract.py`。
source GUI実体のoffscreen試験: `work/v126 GUI 日本語/source-copy-003/source-gui-result.json`。
両GUIの切替、Preset CRUD/選択保持/起動時未選択、job再読込、Scheduler表示再適用を確認。
同じdirectoryの `PHASE1.png` / `PHASE2.png` を目視確認。
offscreen専用試験processでWindowsのMeiryoを明示読込して撮影。配布appのフォント設定は変更していない。

既存テストの変更理由:

- 連続3件no-opで全体停止する旧期待を、個別失敗・他job継続へ変更。
- 保存保留だけが残ったcontrollerの旧終了期待を、明示Stopまで待機する期待へ変更。
- 旧予約jobの不変性テストは将来期限を明示。期限到達時の上限中回収は別contractで検証。
- GUI Fake E2Eの待機時間のみテスト内で加速。productionの600秒値とPause/Stop割込みは別contractで検証。
- Runtime末尾が常にjob.nextになる旧期待を、後続Scheduler表示があっても処理件数通知を確認する期待へ変更。

## Liveの境界

本番 `Dropbox\プロジェクト\個別アプリ開発\DJDmaker_Ver1.2.5\DJDmaker.exe` の起動を確認。
Notebookを開いたChromeも存在する。本番の操作を競合させないため、ユーザーへ終了を依頼。
勝手に本番アプリやChromeを終了していない。V126で実Notebookへの送信・Download・削除はまだ実行していない。

新しい本番保護baselineは、Ver1.2.5のsystem JSON 594件、うちjob JSON 256件。
過去の177件という記録を、現在も正確に177件であるとは読み替えない。
旧Ver1.2.4の保護baseline436 JSONも別途再検証し、変更0だった。
状態保存は引き続きcopy側に限定する。本番起動中のユーザー操作による変更が検出された場合も、
それを自動で巻き戻さない。

## 指定56項目

| # | 項目 | 結果 |
|---|---|---|
| 1 | HEAD before | f2dced0cb0dd88017dcccf7978f6a128677223eb |
| 2 | Chrome history旧ロジック | productionの履歴推測分岐は未発見。上記の検索・現行判定を確認 |
| 3 | current-state実装 | 既存process.poll/context接続/profile lock方式を維持。current-state contract追加 |
| 4 | legacy削除 | 該当する履歴判定分岐未発見のため削除0。Chat履歴保護は維持 |
| 5 | resume button Phase1 | 除去。内部Resumeは維持しPause中のStartから実行 |
| 6 | resume button Phase2 | 同上 |
| 7 | Phase2 Preset root cause | 共通読込・GUI再構築後再表示の欠落を修正。実ユーザー環境の完全な原因確定はlive待ち |
| 8 | shared Preset | 共通Repository/既存CRUD service/ViewModel/JSON。session選択維持・再起動時未選択 |
| 9 | Scheduler旧停止原因 | 上限中local後return、controller旧完了条件 |
| 10 | 新architecture | GUI非依存Capability/task discoveryと既存単一pipelineを統合 |
| 11 | Priority 1 | 未生成・期限到達retryable生成を優先。上限中送信を抑止 |
| 12 | Priority 2 | RAW/Ending任意/HLS/ZIP/保存回復 |
| 13 | Priority 3 | 上限中も期限到達artifact確認、Readyなら同じjobを回収。回収専用コマンドも上限中の早期returnを除去 |
| 14 | Priority 4 | checkpoint診断、persisted retry、他job継続 |
| 15 | Priority 5 | 600秒の割込み可能待機、次cycleで再探索 |
| 16 | Capability | generation/upload/chatとartifact/download/localを分離 |
| 17 | Limit中Generation count | fixtureで0。V126 live未実施 |
| 18 | Limit中Local count | fixtureで完了を確認。live未実施 |
| 19 | Limit中Artifact check count | fixtureで期限到達対象1件のpoll確認。live未実施 |
| 20 | Limit中Download count | fixtureでReady対象1件をDownload。live未実施 |
| 21 | local完了後 | due artifact確認へ継続し、残件ありなら待機 |
| 22 | 10分Wait | 600秒値・即時Pause/Stop割込みをcontract検証。実10分live待ち |
| 23 | Limit解除後Generation | 既存+5分・positive再確認contractを維持。V126 live待ち |
| 24 | Error retry | job別。処理中断や保存失敗を全job成功に変換しない |
| 25 | checkpoint resume | RAW等の検証済み位置から再開 |
| 26 | Unknown fallback | 同じNotebookのensure_sourceへ戻り、既存正常sourceを重複uploadしない。再確認もUNKNOWNなら送信せず失敗を記録 |
| 27 | retry 3回 | persisted回数、coordinator再作成をまたぐ3回contract |
| 28 | terminal failed | 3回失敗した当該jobのみTERMINAL_FAILED。COMPLETEDにしない |
| 29 | auto stop | 全job完了/terminalかつ保存保留なし。将来期限・保存保留では停止しない |
| 30 | Pause | 600秒待機を即時中断、既存side-effect checkpointを維持 |
| 31 | Stop | 次操作を開始せず、既存所有processの終了手順を維持 |
| 32 | Job save isolation | 既存境界を使用。保存保留jobを隔離して継続 |
| 33 | Phase1 GUI | source/offscreen組立・操作・画像確認PASS。実Notebook/EXE acceptanceとは区別 |
| 34 | Phase2 GUI | source/offscreen組立・操作・画像確認PASS。実Notebook/EXE acceptanceとは区別 |
| 35 | Runtime | 優先度、操作可否、task、再試行、次回巡回、terminal件数。切替後再表示 |
| 36 | tests | 最終594 passed / 0 failed、304.01秒。compileall、diff --check PASS |
| 37 | Limit live | 未実施。本番アプリ・Chrome終了確認待ち |
| 38 | Download live | 未実施。自然にReadyがある場合のみ実施予定 |
| 39 | Wait live | 未実施 |
| 40 | Error live | 未実施。copy state側のみ使用予定 |
| 41 | 本番data変更数 | Ver1.2.5の594 JSON（job 256件）hash照合で0。旧Ver1.2.4保護436 JSONも0。元3repo clean |
| 42 | Version | 1.2.5維持。live前に1.2.6化しない |
| 43 | cleanup | 未実施。正式1.2.5配布物を保持 |
| 44 | portable | V126未作成 |
| 45 | EXE Scheduler | V126未実施 |
| 46 | EXE dual GUI | V126未実施 |
| 47 | EXE Preset | V126未実施 |
| 48 | EXE Pause | V126未実施 |
| 49 | package audit | V126 package未作成 |
| 50 | commit | 未実施 |
| 51 | push | 未実施 |
| 52 | HEAD/origin after | fetch後、両方 f2dced0cb0dd88017dcccf7978f6a128677223eb。remoteは https://github.com/G-fontis/DJDmaker.git |
| 53 | ahead/behind | fetch後0/0。phase2 refは f6f5871925f44718f51a106f7f55fa44e8f882b1 のまま |
| 54 | Git status | source/test/docsの未commit変更あり。Git cleanではない |
| 55 | known issues | V126 live/EXE未確認。履歴原因および元のPreset障害の実機因果関係は未確定 |
| 56 | 未解決事項 | 本番終了確認→copy live Limit/回収/待機→Version/build/EXE/package→commit/pushの順で残る |

現時点のrelease判定:

`DJDMAKER_V126_WORK_STEALING_SCHEDULER_RESULT: BLOCKED`

SourceテストをliveまたはEXEのPASSへ読み替えない。

再開条件: 本番Ver1.2.5アプリと専用Chromeを終了し、「閉じました」と返信。
現在のWindow/process/profileを改めて確認した上で、copy側のlive Acceptanceを行う。
この停止時点で本番アプリPID 22148はまだ起動中。Codex側から終了させていない。
