# Ver1.1 credit reservation / recovery / storage記録

指示ID: `DJD-CHAPPY-V11-CREDIT-RESERVATION-RECOVERY-STORAGE-ROBUSTNESS-FULL-001`

## GNB_Creator一次調査

基準repoは`AutoGeminiNoteBookCreator` main HEAD `28bd51dfe2894018bfc9d65a02f219a933199127`。`app/config/selectors.py`、limit detector、video monitor、NotebookLM page、tests、docs、historyを検索した。元実装にはvisible status container内のcredit/limit語検知、生成前の枯渇確認、2秒間隔の状態監視、予約操作の完全一致候補、予約待機表示の確認がある。DJDmakerはこれらを踏襲した。

元repoには、reset時刻のtimezone付きdatetime化、残量percentage、JSON recovery stack、reset前gateは存在しなかった。SQLite実装は今回のDB禁止条件により移植せず、既存`system/jobs/*.json`へ統合した。

## Creditと予約

状態は`CREDIT_AVAILABLE`、`CREDIT_LOW`、`CREDIT_EXHAUSTED`、`CREDIT_UNKNOWN`。source本文ではなくalert/live/status surfaceのみを読む。`HH:mm`は現在時刻より未来なら当日、同時刻以前なら翌日としてtimezone付きで永続化する。

生成前に明示的な枯渇を検知した場合はチャット送信・即時生成を行わない。予約ボタンは完全一致allowlistのみを許可し、promptのDOM readback一致、既存artifactなし、予約後の`SCHEDULED_REMOTE`相当状態をすべて満たして初めて成功とする。予約UI不在と予約失敗は別例外で診断可能にした。

## Recovery

job JSONにNotebook ID/URL、予約時刻、reset時刻、想定生成時刻、最終確認、artifact/download/RAW状態、retry回数を保持する。アプリ終了で消える別stackは作らない。

［未回収動画のチェックから続ける］は予約待機・動画待機・download pending・recovery pendingだけを対象にする。reset前はremote操作0。reset後は既存Notebookのartifact状態を確認し、readyなら既存のDownload→12項目RAW gate→artifact削除→Ending→HLS→ZIPを続行する。`submit`は呼ばず、Notebookと動画の二重生成を禁止する。COMPLETEDは対象外。

## Job JSON WinError根因と修正

根因は動画MP4のopen handleではなく、状態遷移のたびに行う`system/jobs/<job-id>.json`のatomic publishで、Windows Defenderや同期clientが短時間destinationを保持する間に`os.replace(temp, destination)`がWinError 5/32を返すことだった。正確な経路は`PipelineCoordinator._transition/_save`→`JobRepository.save`→`_VersionedDocument.save`→`JsonStore.save`→`JsonStore._replace`。

新方式はresolved job path単位のprocess内`threading.RLock`で直列化し、job JSON用file lockを廃止した。UTF-8 payloadを同一directoryの一意tempへwrite、flush、fsync、handle close、既存正常値をbackup、`os.replace`でpublishする。PermissionError/WinError 5/32だけを100/200/400/800/1600/3200msのbounded retry対象とする。replaceが実行済みなのにWindows filter driverが例外を返しsourceが消える場合は、期待payloadをfsync済みtempとして復元し、安全に再publishする。

内部retry成功時はWARNING/INFOログだけでpipelineを継続し、GUI modalは0。全試行失敗時だけ`JobStateSaveError`をGUI operation errorへ伝え、既存JSON、RAW、Notebook、artifact、outputを削除せず再試行可能にする。
