# Ver1.2.5 上限待機・Pause・共通GUI Release Gate

更新: 実行policy変更後のbuild・最終EXE検証は [Ver1.2.5最終Release Gate](v125-final-release.md) を参照。この文書のBLOCKEDは過去時点の履歴であり、現在の製品Gate結果ではない。

指示ID: DJD-CHAPPY-V125-LIMIT-PAUSE-DUALGUI-COMPONENT-ARCHITECTURE-FULL-001

基準main/origin: `6dd77183ec8eca14412502c432c5e984e0b4bb5c`。
参照phase2: `f6f5871925f44718f51a106f7f55fa44e8f882b1`。
開始時clean、main切替・ff-only pull後も同一HEAD。

## 変更境界

- backendはmainのみ。HUD描画部品はphase2の `gui/hud.py`、layoutはphase2 MainWindowのbuild methodsを `phase2_presentation.py` へ抽出。
- ボタン、Settings/Preset/Job Detail/Log、checkbox/delete/sort、runtime、save deferred、即時row更新は共通MainWindow handlerへbind。HUDの進捗/開始時刻/認証表示も維持。
- 共通 `core/commands.py` に19 Command IDs、payload検証、起動時unique/required検証、10 Event IDs。両GUI required coverage 100%、差分0をtestsで検証。
- GUIはPipeline内部を直接呼ばず、共通AsyncControllerBridge/Application Serviceへ要求。Task layerはGUI typeを参照しない。
- GUI切替は停止中のみ。settings.gui_typeを保存し、job/selection/checkboxを保持。実行接続再構築との競合を避けるためPause中も切替禁止。

## Limitとローカル処理

`UsageLimitDetector` はvisible文言、中央query-boxのdisabled送信button、入力欄状態を読む。生成classは使用しない。source/会話本文を除外。
時刻はOS local timezone。過去のHH:mmは翌日、午前/午後に対応。解除時刻+5分をatomic JSONへ保存。時刻不明は自動再開しない。

production Chat flowの現在quota返信はCloudLimitReachedへ移行し、新規reservationを呼ばない。旧selector/helper/予約テストは維持。旧予約jobは期限後の既存回収経路を維持する。

通常Phase A生成優先→Phase B回収を維持。Limit中はDownloaded→RAW gate→Ending任意→HLS/ZIP→TXT移動、保存復旧を継続。完成確認済みのDownloadだけ許可。動画artifactの削除はローカル進行の前提にせず、Limit中はDELETE_PENDINGとRAW安全gateを保持。Notebook/sourceを削除しない。

backlog空なら1秒以上のイベント待機。再開予定前の生成用navigationは0。期限後に再確認しChat enabledでのみ再開、まだLimitなら新時刻+5分へ延長。確認失敗/Chat未有効は120秒の再確認間隔で待機維持。

## Pause根因と修正

旧実装はcontroller/schedulerのpaused flagだけを変更し、実行中run_cycle内の複数job処理を止めなかった。
共通CancellationTokenへCondition待機を追加。Browser wrapper、queue、source/chat/download/local checkpointが同じtokenで停止する。StopはConditionを解除してキャンセルする。
開始済みFFmpegは完了まで許可し、次checkpointでPauseする。返信/DOM観測時計からPause期間を除外し、Pause後の誤timeoutと重複送信を防ぐ。Limitのwall-clock期限は延長しない。

## 実環境の証跡（2026-09-08、JST）

- 提供HTMLはview-sourceで動的Limit文字列を含まなかった。canonical URLの実DOMを既存BrowserManager/CDP方式で確認した。
- 06:14:47: `AI の使用量上限に達しました。6:21以降にすべての機能が利用可能になります。` / `チャットは 6:21 まで無効になっています` を検出。disabled=true、chat_enabled=false。解除06:21、再開06:26。
- 100予定queueを用いた実GUI Pause試験: 最初のNotebook表示後にPause button→安全checkpoint。Pause中navigation増加0、Preset送信0、reservation0、PAUSED表示あり。Resume後1回の次navigationを確認し、live操作は計2回のread-only訪問で打ち切った。全100件の重複なしResumeはfixtureで別確認。
- 詳細: `work/v125-live-gui-002/report.json`、`pause.png`。初回probeは初期credit更新中のテストfixtureボタン無効を検出して不合格。fixtureがcontroller現在状態を反映してから押すよう訂正し、2回目PASS。製品のPauseを迂回していない。
- 本番Ver1.2.4フォルダの全256 job JSONをhash比較し、変更0。従来指示の177件以外も保護。
- 追加監査: 過去cleanup manifestは旧 `DJDmaker_Ver1.1/system` の177 JSON（jobs 175＋直下2）を参照しているが、現在その旧配置先は存在しない。近隣Ver1.2.3/1.2.4の同名ファイルも全件同一ではなく、旧manifestを現在の前後比較baselineとして流用できない。今回の256件は実環境probe直前/直後に比較した件数であり、過去177件との同一性を証明したという意味ではない。旧配置変更の原因は本作業では断定しない。本作業中のユーザーファイル移動・削除は0。
- 上限中の実動画Download: `work/v125-limit-live/download.mp4`、1,850,725 bytes、h264/aac、1280x720、455.71483秒、size stable、temporaryではないこと、ffprobeを確認。remote artifactは保持。
- その動画のcopyを実GUI/実FFmpegへ入力。`work/v125-live-local-report.json`: RAW全gate=true、Ending SKIPPED、HLS PASS、ZIP作成、COMPLETED、TXT MOVED。未生成100件へのcloud calls=[]、errors=[]。実時刻06:26前のwait維持。
- 同一copy runtimeでPHASE1→PHASE2→PHASE1→PHASE2、settings保存、CMD_PAUSE→CMD_RESUME→CMD_STOPを確認。画像: `work/v125 live local 日本語 002/`。

## 検証状況

- Limit/parser/Restart/Pause/Command/実DOM追加testを実施中。全体試験で検出したRAW削除失敗時の13回帰は修正し、安全gate関連17 testsを再実行してPASS。全体を再実行する。
- 06:26:17〜06:26:26に実Notebookを再確認。Chat enabled=true、copy側Limit解除、本番JSON不変、追加生成0。`work/v125-live-gui-002/release-probe.json`。解除後Phase A実行順はclock-injected fixtureで検証し、追加クレジット消費はしない。
- 全577 tests PASS、hydration待ち追加を含むLimit/GUI契約47 tests PASS、GUI関連59 tests PASS。source/live Gate後にPython/Product 1.2.5・File 1.2.5.0へ更新。更新後の最終全suiteは **578 passed / 0 failed / 253.67秒**。証跡: `work/v125-release-tests.xml`。compileall/diff-check PASS。
- 正式buildは以下のPowerShell実行ポリシーにより開始前に拒否された。portable/package/cleanup/commit/pushは未実施。現時点を正式release/PASSとしない。
- 既知事項を維持: `SOURCE_UPLOAD_ERROR_LIVE_RECOVERY: UNVERIFIED_LIVE`、告知modal自然再現時の自動dismissは未確認。強制再現しない。

## 正式build開始時の拒否

実行対象: `C:\xampp\htdocs\PHP\DJDmaker\packaging\build_windows.ps1`。
既存script/onedir方式、Python=`.venv\Scripts\python.exe`、FFmpeg/ffprobeは保持中のVer1.2.4同梱binary、licenseは既存review済みファイルを指定した。

```text
& : File C:\xampp\htdocs\PHP\DJDmaker\packaging\build_windows.ps1 cannot be loaded because running scripts is disabled
on this system. For more information, see about_Execution_Policies at https:/go.microsoft.com/fwlink/?LinkID=135170.
At line:2 char:124
+ ... Fresh build path required' }; & .\packaging\build_windows.ps1 -Python ...
+                                     ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    + CategoryInfo          : SecurityError: (:) [], PSSecurityException
    + FullyQualifiedErrorId : UnauthorizedAccess
```

拒否主体はPowerShell。`Get-ExecutionPolicy -List` はMachinePolicy/UserPolicy/Process/CurrentUser/LocalMachineすべてUndefined、実効policyはRestricted。管理GPOによる明示設定とは確認されておらず、既定のRestrictedによる拒否である。scriptはNotSigned、Zone.Identifierはなく、通常の`:$DATA` streamだけ。
PyInstaller自体は未起動、`dist/DJDmaker_Ver1.2.5` は未生成。Codex tool/sandboxの拒否ではない。実行policy変更、Bypass、別APIで同じscript内容を再実行する迂回は行っていない。

## 完了報告（release未完了）

返答ID: DJD-CODEX-20260908-V125-RESULT-001

| # | 項目 | 結果 |
|---|---|---|
| 1 | HEAD before | 6dd77183ec8eca14412502c432c5e984e0b4bb5c |
| 2 | 現行UI調査 | 実DOMで新AI上限文言とdisabled中央送信buttonを確認 |
| 3 | HTML DOM | view-sourceには動的上限本文なし。canonical URLでlive診断 |
| 4 | detector | visible text/time/中央Chat disabled。source/会話本文除外 |
| 5 | time parser | OS local timezone、HH:mm/午前午後、日跨ぎ、時刻不明は安全待機 |
| 6 | +5分 | 定数5分。実例06:21→06:26 |
| 7 | persistence | system/cloud-limit.json、atomic/backup、再起動維持 |
| 8 | cloud block | 未生成100件へのsend/upload/生成/予約0 |
| 9 | local fallback | 実動画copyでRAW→Ending skip→HLS/ZIP→TXT move PASS |
| 10 | backlog空 | 期限前は待機。新規Notebook巡回0 |
| 11 | resume check | 06:26後のlive Chat enabled確認。確認不能時は待機維持 |
| 12 | reservation production | Chat flowから新規予約への遷移を除外、呼出0 |
| 13 | reservation source | 旧実装・selector/helper/testを保持 |
| 14 | legacy予約 | 旧state/remote保持、互換回収経路を維持 |
| 15 | Pause根因 | paused flagのみではcycle内部の複数job処理が継続した |
| 16 | Pause修正 | 共通token/Condition/safe checkpoint、Stopでwake |
| 17 | Pause navigation | 実GUI/実Notebookで増加0 |
| 18 | Pause send | 0 |
| 19 | Resume重複 | 100件fixtureで重複0、liveで次navigation1回のみ |
| 20 | Phase1 | 白GUI、source実画面確認 |
| 21 | Phase2 source | phase2 f6f5871925f44718f51a106f7f55fa44e8f882b1 |
| 22 | main統合 | HUD部品/表示layoutのみ、古いbackend移植0 |
| 23 | Phase2補完 | 共通最新操作・runtime・save deferred・checkbox/delete/sort・進捗/開始時刻 |
| 24 | switch | 停止中のみ。表示接続再構築の競合防止 |
| 25 | setting | gui_type保存、両mode往復と復元PASS |
| 26 | Command IDs | SETTINGS_OPEN / GOOGLE_LOGIN / CLASS_VIDEO_START / SCRIPT_RELOAD / RECOVERY_START / PAUSE / RESUME / STOP / LOG_OPEN / JOB_DETAIL_OPEN / DELETE_SELECTED / DELETE_COMPLETED / GUI_SWITCH_PHASE1 / GUI_SWITCH_PHASE2 / INPUT_OPEN / RAW_OPEN / OUTPUT_OPEN / ENDING_CHANGE / ENDING_PREVIEW（全てCMD_ prefix） |
| 27 | registry | 19 unique IDs、共通Button registry |
| 28 | dispatcher | 共通CommandRouter→共通handler→Application Service |
| 29 | payload | DELETE job_ids[]、GUI type整合、余分/不正payload拒否 |
| 30 | unknown | INTERFACE_ERROR、handler0、GUI safe-disable、backend無条件killなし |
| 31 | duplicate | 起動時InterfaceError |
| 32 | Phase1 coverage | required IDs 100% |
| 33 | Phase2 coverage | required IDs 100% |
| 34 | set diff | 0、実button.click契約test |
| 35 | Event IDs | 共通10 IDs、immutable envelope、Qt threadで描画 |
| 36 | ViewModel | Job/CreditLimit、既存共通runtime変換を再利用 |
| 37 | task GUI依存 | GUI type/Qtへの依存なし |
| 38 | Phase A/B | 通常生成優先、Limit時のみlocal fallback |
| 39 | save isolation | 既存隔離・local復旧・他job継続tests維持 |
| 40 | Limit live | 文言/時刻/disabled/実Download/自然解除を確認 |
| 41 | Pause live | GUI button→checkpoint→navigation0→Resume PASS |
| 42 | switch live | 同じcopy runtimeでPHASE1→PHASE2→PHASE1→PHASE2 PASS |
| 43 | tests | 最終Ver1.2.5: 578 passed、0 failed。work/v125-release-tests.xml |
| 44 | regression | Phase A/B、RAW、任意Ending、HLS/ZIP、Stop/Close等を全suiteで確認 |
| 45 | Version | source候補1.2.5、File metadata 1.2.5.0。正式配布はまだ1.2.4 |
| 46 | cleanup | 新版portable未検証のため未実施。旧1.2.4・user data保持 |
| 47 | portable | build scriptが実行policy拒否、未生成 |
| 48 | EXE dual GUI | 未実施。source実GUIではPASS |
| 49 | EXE Limit | 未実施。source実環境/fixtureではPASS |
| 50 | EXE Pause | 未実施。source実GUIではPASS |
| 51 | package audit | 新package未生成につき未実施 |
| 52 | commit | 未実施 |
| 53 | push | 未実施 |
| 54 | HEAD/origin | 6dd77183ec8eca14412502c432c5e984e0b4bb5cのまま |
| 55 | ahead/behind | 0/0（既存origin/main refとの比較） |
| 56 | Git status | 今回のsource/test/docs/metadataが未commit。cleanではない |
| 57 | phase2 ref | f6f5871925f44718f51a106f7f55fa44e8f882b1を保持 |
| 58 | known issues | source upload error live recovery / announcement modal live自動dismissはUNVERIFIED_LIVEを維持 |
| 59 | 未解決 | build script実行許可、portable/package/cleanup/commit/push Gate |
| 60 | release readiness | 未準備。Ver1.2.5の配布・正式PASSは禁止 |

保持中の正式Ver1.2.4成果物も既報SHAと一致:

```text
2CF89913DAAB07C9A63B1580B2109BE555B340F6C801B9E23B3A70CAAE11D634  DJDmaker_Ver1.2.4/DJDmaker.exe
D1B2DB938BD157321F46CCAC0909113C43BBB702F7D37ED88520B1EA8D29610B  DJDmaker_Ver1.2.4.zip
```

DJDMAKER_V125_LIMIT_PAUSE_DUALGUI_COMPONENT_RESULT: BLOCKED

## 正式build実行許可後の再試行（2026-09-08）

返答ID: DJD-CODEX-20260908-V125-BUILD-AUTHORIZED-002

今回指示ID: DJD-CHAPPY-V125-BUILD-EXECUTION-AUTHORIZED-001

ユーザーによる既存正式buildスクリプトの実行許可を受領し、同じscript・引数で通常実行を再試行した。上記60項目のうちsource/live結果は既存証跡であり、この再試行でtestsやlive試験を再実行したものではない。

```text
& : File C:\xampp\htdocs\PHP\DJDmaker\packaging\build_windows.ps1 cannot be loaded because running scripts is disabled
on this system. For more information, see about_Execution_Policies at https:/go.microsoft.com/fwlink/?LinkID=135170.
At line:4 char:3
+ & .\packaging\build_windows.ps1 -Python 'C:\xampp\htdocs\PHP\DJDmaker ...
+   ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    + CategoryInfo          : SecurityError: (:) [], ParentContainsErrorRecordException
    + FullyQualifiedErrorId : UnauthorizedAccess
```

| 追加報告項目 | 結果 |
|---|---|
| repository / branch | C:\xampp\htdocs\PHP\DJDmaker / main |
| build script実行結果 | exit 1。PowerShellがscript読込前に拒否。PyInstaller未実行 |
| ExecutionPolicy拒否解消 | 未解消。全scope Undefined、実効Restricted |
| 許可と環境設定 | 実行の明示許可は受領済み。ただし環境の実行policyは変わっていない |
| Portable path | dist/DJDmaker_Ver1.2.5（予定。未生成） |
| EXE SHA / ZIP SHA | 未生成につき算出不可 |
| EXE dual GUI / 選択再起動保持 | 未実施 |
| EXE limit / +5分 / cloud送信0 / local backlog | 未実施。既存source/live証跡は上記参照 |
| EXE Pause / Resume / Stop / Close / 保存隔離 | 未実施 |
| EXE Ending任意 / HLS / ZIP / package監査 | 未実施 |
| tests / Version | 既存578 PASS証跡維持、source候補1.2.5 / File 1.2.5.0。今回再試験なし |
| commit / push | 未実施。全Gate未達のため禁止を維持 |
| HEAD / origin/main | 6dd77183ec8eca14412502c432c5e984e0b4bb5c（ローカルref確認。今回fetch/公開GitHub照合なし） |
| Git clean | 未commit変更あり。cleanではない |
| 禁止操作 | force push / rebase / reset / amendなし。削除なし |
| 次に必要な対応 | 環境管理側で、対象scriptの実行を許容する実行policyを正式に設定する必要がある |

実行policy変更やBypass、別経路によるscript実行は行っていない。今回の停止原因はコード不具合ではなく、未解消のPowerShell実行policy拒否。全Gate PASSとは報告しない。

DJDMAKER_V125_FINAL_BUILD_RELEASE_RESULT: BLOCKED
