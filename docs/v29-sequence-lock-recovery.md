# Ver2.9 Job No sequence lock復旧

基準はmain `cd22e38`（Ver2.8）、1102 tests PASSです。採番の一意性、単調増加、削除番号の非再利用を維持し、一時的なcounter競合をpipeline fatalから切り離します。

## 実環境と原因

指定Dropbox配布先で、正式Ver2.8と同一SHAのEXE、PID3812を確認しました。Jobは3件、番号1〜3、next_job_no=4、重複／欠落0です。sequenceとlegacy lockのSHA・内容・timestampを読取監査し、lockに同PIDが記録され、最後のJob保存後もmarkerが残っていました。Restart Managerのresource owner一覧はsequence／lockとも空でした。Repository側は16 Job、max16／next17、重複0です。本番stateは変更していません。

旧`_document_lock`はO_EXCLによるfile存在方式で、handleは取得直後に閉じ、最後にunlinkします。解放時のsharing/permission errorでunlinkが失敗すると、PIDが生きたままmarkerが残ります。次回は5秒のtimeoutがRepositoryLockTimeoutとなり、reloadの汎用GUI error channelやpipeline例外へ伝播しました。この連鎖は旧実装へ解放故障を注入して再現しています。実障害時のWinErrorは旧版に保存されておらず、Dropboxがその瞬間に干渉したこと自体は断定しません。

加えて、取得deadlineはprocess内RLock待機前に開始し、RLock取得自体は無期限でした。stale判定は60秒経過だけでowner生存を確認せず、live ownerのmarkerを除去できました。採番のlock内には全Job／削除履歴走査、Job publishとその保存retryがあり、起動時migrationも毎回write lockを取得しました。これらをsequence専用経路で解消します。他のrepositoryのlock仕様を一律変更しません。

## 新しい排他と短いscope

sequence専用context managerはpath-keyed process内RLockと、Windowsのnonblocking OS byte-range lockを併用します。同一threadの再入は既存handleを共有します。markerはowner PID、process start、created_at、host、releasedを記録しますが、排他の根拠はhandleです。markerが残ってもfile存在だけではbusyにしません。成功／例外／metadata書込失敗でも、finallyでunlock・close・local releaseをそれぞれ保証します。process crashでもOSがhandle lockを解放します。

旧形式markerのPIDがliveまたは不明の場合は取得せず待機します。PID再利用／時計補正の疑いも不明扱いとし、年齢だけで回収しません。旧ownerが存在しないと確認できた場合だけ、同じfileをOS guardとして再利用します。live ownerのfileを削除しません。

Job／削除履歴／退避Jobの走査はlock取得前、Job publishと保存retryはlock解放後です。critical sectionにはcounter primary／固定backupの読取、番号予約、atomic更新とflushだけを置きます。予約はjob_id→job_noも保存するので、最初のJob publishが競合・失敗しても同じIDへ別番号を振りません。既存正常Jobのsaveは従来どおりsequence write lock不要です。

取得・sharing conflictは50/100/200/400/800/1600ms、計7 attemptのbounded retryです。process内mutexも1回50ms以内の取得試行にし、retry waitはhandle／mutexを解放した後に行います。writeは同一directory temp→flush/fsync→close→atomic replace。sequenceだけはprocess/thread別の固定temp tagを使い、失敗したretry／pollでtempが増殖しないようにします。通常Job保存の従来のrecovery temp保持は変更しません。backupは1個の固定`.bak`です。

参考: [Microsoft LockFileEx](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex)、[Python msvcrt.locking](https://docs.python.org/3/library/msvcrt.html)。Dropbox同期の別PC replica間の分散排他を保証するものではなく、同じfilesystemのsystem directoryを共有するprocess間排他です。

## Read-only reloadと待機

一覧／sort／詳細はsequenceをwrite lockしません。startupも全番号が一意かつcounterが正常なら、Job JSONを変更せずreadだけで完了します。新規入力があった場合だけ採番します。

bounded retry後の競合はWAITING_FOR_JOBNO_LOCKとして通常GUIへ表示し、blocking error／Job FAILED／USER_STOPに変換しません。pending importは安定したJob IDを保持し、共通controllerが1秒間隔で再確認します。owner解放後は自動でreload／採番し、schedulerをwakeします。採番待ちのimportがある間は、Job数0や既存Jobの完了だけでpipelineを終了させません。既存の他Jobは継続します。Pause/Stop/Closeは既存tokenを維持し、Close時はrecheck workerも停止・joinします。

未採番legacy Jobの保存中に競合した場合も、既存のdeferred保存隔離へ移し、FAILEDにしません。sequence復旧後にsequence由来の保留だけを再照合可能にし、一般の保存retry budgetは変更しません。

## Recoveryと不変条件

sequence欠落・JSON破損は、lock外で読んだ全Job／削除履歴／退避Jobのmax＋1と、正常backupのhigh-waterから安全に再構築します。既存番号やNotebook identity／checkpoint／pathを変更しません。以前使った番号の再利用を避けるため、正常な保存済みhigh-waterがmaxより高ければそちらを維持します。読めないJob、future schema、矛盾した予約は黙って除外せず明示診断します。

## Gate

owner生存／死亡／不明、時計・PIDの曖昧さ、再入、例外時解放、process crash、同時process採番、read-only reload、待機からの自動復帰、pipeline継続、backup復旧、既存Job byte不変、Windowsの実sharing conflictを試験します。Live Lock Gateは隔離した実Qt GUIと複数processで行い、production fileにはfault injectionしません。source/live Gate後にのみVer2.9へ昇格し、portable・EXE・package Gate後に通常commit/pushします。

昇格前とVer2.9昇格後の全1129 testsがPASSしました。新規はsequence23件とreload/controller4件の計27件です。backup公開失敗時も同一Job予約を保持し、保存保留journalの番号をhigh-waterへ含めて再利用を防ぎます。EXEの14系統とZIP展開後portableもPASSし、指定の15機能を確認しました。

実EXE Live Lock Gateでは、別process owner保持中のread-only reload成功、通常GUIのWAITING_FOR_JOBNO_LOCK、dialog/error各0、owner解放後の自動再開、別processの同時採番3/4、例外後の次回採番、backupからの再構築を確認しました。両管理領域の本番Job No重複0、監査対象57 filesのSHA変更0、既存Job JSON／counter／live legacy markerの変更0です。

新Ver2.9の全Gate完了まで旧Ver2.8 ZIP／SHAを保持しました。Repository内の旧Ver2.8 onedirは検査時点で存在しませんでした。記録済みSHAと全archive CRC、public配布物だけであることを確認後、旧ZIP／SHAと今回生成したbuild／検証copy／fixtureをmanifest付きで整理しました。40,695 files／21,856 directories、7,599,526,215 bytes、削除失敗0。readonly等7項目と本番dataを保持し、属性・ACL・policyを変更していません。最終distはVer2.9 folder・ZIP・SHAのみです。

Dropbox側の稼働中Ver2.8は停止・置換せず保持しています。Ver2.9へ切り替える際は旧版を通常終了してください。legacy ownerが終了した後は、新版が安全にguardを再利用します。実障害時のDropbox sharing WinErrorの記録はないため、環境上の瞬間的な干渉については未確定として区別します。
