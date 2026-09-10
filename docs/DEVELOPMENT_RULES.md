# 開発ルール

## Ver1.2.8 優先指示とCurrent Window恒久仕様

- `DJD-CHAPPY-V128-STARTUP-COLLISION-STOP-REASON-CURRENT-WINDOW-FULL-001`を優先する。source/live Gate前はVer1.2.7を維持し、全Gate前のcommit/pushは禁止。本番検証はコピーで行い、原本・メディア・profileを変更しない。
- 「Google/Chrome起動Window確認は、過去のclose/open履歴を参照せず、現在そのWindow/Processが存在するかだけで判定する。ユーザーの明示的な変更指示がない限り、この判定ロジックを変更してはならない。」AST/動作contractで履歴への依存を防止する。
- 出力所有権は現在のjob identityと実際の出力pathから全体照合してから保存する。同一sourceの重複読込は排他し、旧重複レコードは元IDを保持した非実行参照として統合する。正常成果物を上書きしない。真の競合のみ対象jobをblockedとし、他jobを継続する。
- 自動終了直前に全jobを再走査する。未完了・将来待機・再試行可能・状態不明・保存保留は完了ではない。全有効jobがCOMPLETEDまたは再試行枯渇TERMINAL_FAILEDの場合のみ全行程終了。単なるFATAL_FAILEDという旧分類だけで完了扱いしない。
- 全exit pathに停止理由codeと日本語messageを持ち、両GUIへ共通表示する。Pause/待機は停止と区別する。

## Ver1.2.7 優先指示

- `DJD-CHAPPY-V127-SOURCE-FAILED-RETRY-QUOTA-PRIORITY-FULL-001` を適用。調査・実装中は1.2.6を維持し、source/live Gate前のversion変更・build、全Gate前のcommit/pushは禁止。
- SourceをABSENT/UPLOADING/READY/FAILED/UNKNOWNに区別する。失敗を読み込み待ちにしない。対象を一意に再診断し、必要な失敗entryのみ除去して同TXTを再uploadする。正常source、Notebook、元TXTは削除しない。retryはjobへ永続化し最大3回、UNKNOWN/timeoutは再分類する。
- failed video artifactはjob snapshot本文を中央Chatへ再送して復旧する。Studio再試行buttonは使用しない。再送前の現在状態照合、最大3回、上限中pending保持を必須とする。
- 各taskの安全な完了境界でCapability/優先順位を再評価する。実行中Localをkillせず、Cloud復帰＋未生成ありなら次Localより生成を優先する。生成→Local→Artifact/Download→Error→10分待機を維持。
- almostは非停止。前回確定の今回返信クォータ不足もCloud停止理由として維持し、確認/Download可否と分離する。本番JSON・phase2・元3repoを変更しない。自然条件がないlive項目はclock/capability injectionで区別して検証する。

## Ver1.2.6 優先指示

- 2026-09-08追加確定: 今回のPreset送信に対応する新しい返信が「動画生成に必要な利用枠（クォータ）が不足」と示す場合も生成停止トリガーとする。実上限banner・Chat無効化を必須にせず、生成を後回しにしてLocal→完成確認/Downloadへ進む。新規予約・即時再送は行わない。過去返信やalmost警告だけでは発火させない。
- 2026-09-08ユーザー補正: 完成動画の確認・Downloadの可否はクォータ条件と無関係。クォータ不足中も各jobの再確認期限とartifact状態に基づき回収する。クォータ解除・Chat有効化を回収の前提にしない。Pause/Stop、認証・実際のアクセス可否、RAW安全gateは維持する。
- 2026-09-08 live補正: almost警告中の送信直後、「回答しています…」によるtextarea disabled/readOnlyを実上限と誤認しない。almostとdisabled属性だけではCloud gateを閉じず、実上限到達・上限によるChat無効の明示表示を必要とする。明示hard limitが併存する場合はそちらを優先する。

- 2026-09-08ユーザー確定: `You're almost at your AI usage limit. Limit resets at 16:21.` は上限接近警告であり、Chatが有効なら生成を継続する。実際の上限到達表示または上限表示に伴うChat無効化で生成待機へ移る。空入力による送信button無効をChat無効化と混同しない。警告はruntime表示・ログへ出し、job checkpointやhard limit待機を警告だけで変更しない。

- `DJD-CHAPPY-V126-CURRENT-WINDOW-DUALGUI-PRESET-WORK-STEALING-SCHEDULER-FULL-001`を優先する。Chromeは現在process/profile/window状態で判定し、過去open/close結果は診断専用。版数はsource/live Gate後まで1.2.5を維持する。
- 生成→local→期限到達artifact確認/Download→Error再診断→10分interruptible waitの優先順位を共通backendで実行する。Chat上限は生成だけを止め、artifactへの現在アクセス可否とは分離する。
- 未完了/将来待ちjobがあるだけで自動終了しない。Errorはcheckpoint照合後に再開し、正常sourceを重複投入しない。再試行は永続最大3回、失敗jobのみterminalとし、成功件数とは区別する。
- 両GUIの「再開」buttonだけを除去し、内部Resumeは維持する。Pause解除は既存の開始操作から行う。GUI切替時は共通job/runtime/preset snapshotを自動表示し、session内選択を共有する。アプリ再起動時のpreset選択は空のまま。
- 全579 tests以上・source/live/EXE/package Gate PASS前はcommit/push禁止。本番原本は読み取りだけ、試験保存はcopy側。元3repoとphase2参照は変更しない。

## Ver1.2.5 上限待機・Pause・Dual GUI（今回の優先指示）

- 指示 `DJD-CHAPPY-V125-LIMIT-PAUSE-DUALGUI-COMPONENT-ARCHITECTURE-FULL-001` を優先する。main Ver1.2.4 backendを正本とし、phase2 `f6f5871925f44718f51a106f7f55fa44e8f882b1` からpresentationのみ取り込む。phase2 ref自体と元3repoは変更しない。
- visibleなAI使用量上限/Chat無効表示・時刻・disabled状態を検出する。source/会話本文は根拠にしない。OS local時刻で解除時刻を解釈し、固定5分を加えて `system/cloud-limit.json` にatomic保存する。
- 再開予定前の新規Notebook・source upload・Preset/Chat送信・生成・新規予約は禁止。予約実装は履歴互換として保持するがproduction Chat flowから呼ばない。以前の予約開始指示より本規則を優先する。
- 通常Phase A→Bを維持。Limit時はローカルRAW検証/Ending任意/HLS/ZIP/TXT移動/保存復旧を優先する。2026-09-08のliveで上限中のWeb動画Downloadを確認したため、完成確認済みDownload待ちの回収を許可する。ローカル処理のためにremote artifact削除を必須にせず、未削除状態とRAW gateを保持する。
- backlogが空なら待機し、期限前のNotebook巡回は行わない。期限後もlimit表示消失とChat enabledの再確認が必要。確認不能時は待機を維持する。
- Pauseは共通CancellationTokenのConditionでsafe checkpointを停止する。新しいgoto/送信/upload/Download/FFmpeg開始は禁止。開始済みの短い操作・FFmpegは終了checkpointまで許可し、その後はResumeまたはStopまで待つ。観測timeoutからPause時間を除外するが、Limitの実時刻deadlineは変更しない。
- PHASE1/PHASE2は同じCommand ID/Router/Event/ViewModelを使う。GUI切替は処理停止中のみ。表示widget再構築中の実行接続の混在を避けるため、Pause中も切替しない。選択GUIのみsettingsへ保存し、job状態を変更しない。
- action追加時はID、payload schema、共通handler、両GUI binding、required-command/誤mapping/unknown-ID testを同時に追加する。ID重複は起動エラー、unknown/不正payloadはINTERFACE_ERRORとしてGUI action dispatchを無効化する。backendを無条件killしない。
- source/test/live Gate完了前のversion bump/buildは禁止。portable Gate完了前のcommit/pushは禁止。既存531 tests・状態保存隔離・RAW安全性・Ending任意・Stop/Closeを維持する。
- 本番job JSONは変更せず、Acceptanceはcopy側stateだけを保存する。実際のVer1.2.4運用folderに2026-09-08時点で256 JSONを確認しており、旧指示の177件に限定せず全件を保護する。

## Ver1.2.4 状態保存失敗のjob単位隔離（最新指示）

- `DJD-CHAPPY-V123-MAIN-JOBSTATE-SAVE-FAILURE-ISOLATION-CONTINUE-FULL-001`によりmain修正を明示許可。基準f35ba708、phase2はf6f58719のまま変更しない。
- 既存atomic保存・backup・6段階retryを維持し、retry枯渇後は対象jobだけ隔離。正式stateを偽更新せず、別のruntime overlayで状態保存待ちを表示する。
- 再試行前にremote/local成果物を照合し、Preset/生成/予約/Downloadを盲目的に繰り返さない。journal失敗時はmemory保持し、未解決件数を最終警告する。
- Phase A/B、Stop/Close、RAW安全性とEnding任意を維持。本番ではfault injectionせず、copy/fixtureのみ使用。全source/test Gate前のversion変更・build、全Gate前のcommit/pushは禁止。

## Ver1.2.3候補：生成優先と状態表即時更新

- 最新正式指示`DJD-CHAPPY-V123-GENERATION-FIRST-CLOUD-THEN-LOCAL-LIVE-STATUS-REFRESH-FULL-001`を優先する。過去の完成artifact即回収規則はPhase B内に限定する。
- Phase Aは未生成jobを1件ずつcheck→生成開始/予約→保存→next。全jobの投入完了または理由確定terminal後にPhase Bへ進む。Phase A中のDownload/Ending/HLS/ZIPは禁止。
- 状態と工程をatomic JSONへ保存成功後、snapshot eventをQt signalでGUI threadへ渡し、job_idで該当rowと集計を即更新する。全表再構築・workerからQt widget直接操作・保存失敗時の成功表示は禁止。
- sort/checkbox/scroll/選択rowを保持し、stop/close/RAW gate/Ending任意/元3engine機能を維持する。本番JSONは変更せず、test/copyで検証する。全Gate通過前のversion bump/build/commit/pushは禁止。

## Ver1.2.2候補：逐次再開と可視化

- 最終release指示`DJD-CHAPPY-V122-FINAL-RELEASE-BUILD-COMMIT-PUSH-FULL-001`では、既存live証跡を引き継ぎ、source/portable/移行/監査Gate通過後にVer1.2.2としてcommit・main通常pushする。追加の動画生成やPhase2統合は行わない。旧配布物cleanupが環境ポリシーに拒否された場合は迂回せず、対象・容量・保持理由をrelease記録へ残す。

- 指示 `DJD-CHAPPY-V121-SEQUENTIAL-RESUME-RUNTIME-VISIBILITY-MODAL-FULL-001` を適用する。通常Startの全件remote pre-scanは禁止。各jobを確認・判断保存・必要工程実行・結果保存してから次Notebookへ進む。
- COMPLETEDと自動再開不可FAILEDはremoteを開かない。待機は既存の永続`next_poll_at`（要求のnext_check_atに相当）を使う。期限前に開かない。
- 白GUIへ現在job/Notebook/phase/stage/decision/next action/outcome/attempt/elapsed/countと一般利用者向けmessageを表示する。Phase2 HUDは変更しない。
- 完成artifactは同一jobでDownload→RAW gate→artifact削除→Ending任意→HLS/ZIP→TXT移動まで進める。すでにローカルにあるRAWのFFmpeg並列数1/2は維持する。
- 不明状態・no-opを黙って巡回しない。理由をJSONとGUIへ残し、3件連続なら安全停止する。告知modalで次Notebookへskipせず、閉じて操作可能を確認するか安全停止する。
- 本番JSONをAcceptanceで書き換えない。コピー側で本番artifactを削除すると本番側が未回収になるため、live削除試験は本番処理から切り離した対象で行う。
- 2026-09-07追加承認: 本番の既存生成失敗Notebookをlive送信試験に使用可。まず1件、Quota履歴/未送信を両方確認できない場合のみ最大2件。state保存はtest/copy側だけとし、原本177 JSONを保持する。生成成功後の動画はremoteに残し、後から通常の未回収/Download処理で回収する。
- 続行指示`DJD-CHAPPY-V122-SEQUENTIAL-RECOVERY-LIVE-CONTINUE-FULL-001`: TAX120/HT075はcopy側の保存済み期限到達後に再確認する。完成を検出した1件について実Download→12項目RAW gate→artifact削除→Ending任意→HLS/ZIP→TXT moveを許可する。原本JSONは変更せず、完成RAW/ZIPとcopy checkpointの所在を必ず引き継ぐ。Notebook/sourceの削除はしない。
- source/live Gateをすべて満たすまでVersion変更・build・commit・pushを行わない。modal自然再現がない場合の`UNVERIFIED_LIVE`は許容するが、他の4種live Gateを免除しない。

## 作業終了通知音

- 開発作業を中断するとき、および依頼された作業を完了するときは、次の音源を再生する。

  `C:\Users\Ichiro\Music\Free音源\finish.mp3`

- 再生時にGUI、メディアプレイヤー画面、コンソール画面を表示しない。
- Windowsでは画面を持たない音声再生API、または非表示・音声専用モードのprocessを使用する。
- 音源の不存在や再生環境の問題で再生できない場合は、成果物やジョブ状態を変更せず、完了報告に再生失敗を明記する。
## 音源再生の並行タスク制御

- Codexがコマンド、検証、実装、監視などの処理を継続している間は、完了音・中断音を再生しない。
- 親タスクが未完了でも、ログイン、確認、選択などユーザーの応答が必要となりCodex側の処理が停止した時点では、中断通知として指定音源を必ず1回再生する。
- サブタスク単位の完了では音を再生しない。全タスクが終了したことを確認してから、全体の中断時または完了時に限り指定音源を1回再生する。
- 並行処理時は、全agentの状態を確認してから再生可否を判断する。

## 認証・ブラウザ変更のRelease Gate

- 2026-09-07追加指示: Endingファイル選択は動作必須条件にしない。未選択時はEnding結合をスキップし、再検証したRAWを変更せずHLS/ZIPへ渡す。選択済みファイルが消失した場合は未選択と混同せず、再選択・設定解除を案内する。
- 2026-09-07補正: 告知modalのlive自動dismiss未確認は既知事項として許容し、fixtureで安全性を担保する。強制再現をしない。他のStop/Close/配布Gateは免除されない。

- 認証またはブラウザ起動方式を変更するreleaseでは、既存Cookie/sessionをコピーしないFresh Profile Sign-in Acceptanceを必須とする。
- Warm profileの成功だけをFresh認証の証拠にしてはならない。Fresh、Warm、期限切れsessionを分けて記録する。
- 認証用Chromeではremote debugging、CDP、Playwright、headless、automation目的のflagを使用せず、password、Cookie、token、Google login DOMを取得・操作しない。
- Start時のPre-flightは完全な内部処理とし、正常時のユーザー操作を増やさない。正常フローはGoogleログインと授業動画作成開始の2操作だけとする。
- Pre-flight全項目がPASSするまでNotebook作成、source投入、動画生成を開始しない。
- 動画生成presetは、選択本文をjobへスナップショットし、source ready確認後にNotebookLMのメインチャットへ完全一致で送信する。管理UIだけを実装して生成経路へ接続しない状態をPASSにしない。
- preset JSONはsettingsと同じprocess内mutex、temporary、flush/fsync、atomic replace、backup/recoveryを使い、保存用lock fileへ依存させない。
- preset一覧は保存するが選択IDはprocess内だけに保持し、アプリ起動時は必ず未選択にする。Start時に未選択ならNotebook作成前に停止し、default・test・前回値へfallbackしない。
- job開始時にpreset ID、名前、本文snapshot、本文SHA-256を保存する。Notebookのメインチャットへsnapshot本文をfillした直後にDOM readbackを行い、完全一致しない場合は`PRESET_APPLY_MISMATCH`で送信せず停止する。送信後はuser messageの安定表示とNotebook側の動画artifact生成開始を確認する。通常pipelineから動画解説カードやGenerateボタンを直接操作しない。
- Codexへの指示に修正・追加が発生した場合、一部差し替え・追記方式は禁止する。必ず最新内容をすべてマージした全文完成版指示を新しい指示IDで再発行し、Codexへは最新版全文だけを送る。この規則はDJDmaker以外のCodex連携開発にも適用する。

## EXE Build Cleanup

- EXE化アプリのversion更新時は、旧portable、旧build中間物、旧配布ZIPなど再生成可能な大容量成果物を、安全確認後に削除する。
- 旧version binaryを`dist`、`build`、検証用一時directoryへ無制限に残さない。
- 削除前に、対応source commit、version、必要なSHA-256、再build手順、ユーザーデータでないことを確認する。
- source、Git履歴、release文書、checksum記録、build script、spec、config template、test fixtureは保持する。
- `raw_files`、`output`、ユーザー設定、利用中browser profileなどユーザーデータをBuild Cleanupで削除しない。

## Credit reservation / recovery

- 最終Gate補正`DJD-CHAPPY-V12-FINAL-RELEASE-GATE-CONTINUE-FULL-001`ではsource upload errorのlive復旧だけを`UNVERIFIED_LIVE`として許容する。原因不明の完了を修正成功とせず、fixture安全性確認と自然再発時の追加Acceptanceを維持する。現在のrelease記録は`docs/v12-final-release-gate.md`を参照する。
- Ver1.2候補（指示002）では、human Start時に過去のquota返信だけで予約へ送らない。既存artifactを先に診断し、生成未開始なら中央Chatへ今回のpreset snapshotを送信し、そのuser message以降の返信だけを分類する。現行候補のrelease判定は`docs/v12-quota-resume-migration.md`に記録する。
- 中央Chatのcontainer配下で入力先を確定する。source検索欄を除外し、全文readback、送信button有効化、今回user message、今回replyを順に確認する。最大3attempt、返信待機は元GNBの180秒を基準とし、再送直前に遅延返信を確認する。
- FAILEDを一律に新規job/Notebookへ置換しない。既存remote・RAW等を診断し、同じjob IDとpreset snapshotを保持して再開する。COMPLETEDは再生成しない。
- 完成TXTの移動失敗はCOMPLETEDを変更しない。同名異内容は上書きせず補助状態を記録する。完成job一覧削除では成果物とremoteを保持し、削除記録をJSONへ残して再読込による復活を防ぐ。

- NotebookLMのクレジット枯渇はsource本文ではなく、visibleなstatus/alert/live surfaceの明示表示だけで判定する。残量percentageが取得できなくても枯渇表示を優先する。
- 枯渇時に即時生成を反復しない。同一jobで即時生成と予約生成を二重実行しない。
- 予約成功は完全一致した予約actionの実行後、remoteの予約待機状態を確認して確定する。
- 予約・未回収情報は既存`system/jobs/*.json`へ永続化する。DBや終了時に失われるmemory-only stackを導入しない。
- 未回収チェックは既存Notebook/artifactのみを対象とし、新規Notebook作成・動画再生成を行わない。クォータreset前でも完成確認・Downloadを行い、COMPLETEDは対象外とする。

## Job state JSON persistence

- job JSONはresolved path単位のprocess内mutexで直列化する。job保存にfilesystem lock fileを使わない。
- atomic saveは同一directory tempへのwrite、flush、fsync、handle close、backup、`os.replace`の順を守る。
- PermissionErrorおよびWinError 5/32だけをbounded retryする。復旧成功時はWARNING/INFOログだけとし、GUI modalやpipeline failureを発生させない。
- 全retry失敗時は対象jobだけ状態保存待ちへ隔離し、blocking modalなしで他jobを続行する。別journal/memoryへ復旧情報を残し、bounded照合後も未解決なら件数を警告する。既存JSON、RAW、Notebook、artifact、outputを削除・破損させない。
