# 開発ルール

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
- 未回収チェックは既存Notebook/artifactのみを対象とし、新規Notebook作成・動画再生成を行わない。reset前はremote操作を行わず、COMPLETEDは対象外とする。

## Job state JSON persistence

- job JSONはresolved path単位のprocess内mutexで直列化する。job保存にfilesystem lock fileを使わない。
- atomic saveは同一directory tempへのwrite、flush、fsync、handle close、backup、`os.replace`の順を守る。
- PermissionErrorおよびWinError 5/32だけをbounded retryする。復旧成功時はWARNING/INFOログだけとし、GUI modalやpipeline failureを発生させない。
- 全retry失敗時だけterminal errorを表示する。既存JSON、RAW、Notebook、artifact、outputを削除・破損させず、再試行可能性を残す。
