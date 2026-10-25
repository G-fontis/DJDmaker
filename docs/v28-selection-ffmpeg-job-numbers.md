# Ver2.8: Job選択・FFmpeg起動失敗・永続Job No

基準はmain `9faa609`（Ver2.7）、既存1064 tests PASSです。Studio Direct Generation、Unknown modalの人間確認、Current Window、RAW single storage、Download guard、保存失敗隔離、各local optionを維持します。

## 全選択／解除

両Phaseへ「すべて選択」「すべて解除」を追加しました。共通Command ID `CMD_SELECT_ALL`／`CMD_CLEAR_ALL`、MainWindowの共通handler、JobSelectionServiceの同じsession selectionを使用します。現在表示中でenabled/checkableなrowを対象とし、CompletedやErrorを除外しません。Job JSONや状態は変更せず、既存の保存リトライ・削除へ同じID集合を渡します。

## FFmpeg process initialization

旧EndingとVideoValidatorはshell=False／capture_output／timeoutでしたが、HLSと異なりCREATE_NO_WINDOWを指定せず、Windows error modeの継承や起動失敗statusの専用分類もありませんでした。起動失敗が汎用MEDIA_STAGE_FAILEDへ丸められ、専用のcommand retry budgetもありませんでした。

0xc0000142（符号付き-1073741502も含む）はDLL initialization failureとしてFFMPEG_PROCESS_START_FAILEDへ分類します。初回を含め4 attempt、retry backoffは2・5・10秒です。Jobのattempt_by_stageへstate／command purposeごとの失敗回数を保存し、再起動で復活させません。retry前の保存が失敗した場合は既存のJobStateSave isolationへ移ります。成功済みcommandを再実行せず、失敗したcommandだけを再実行します。4失敗後はFFMPEG_RETRY_EXHAUSTEDとしてそのJobのみterminalにし、他Jobを続けます。USER_STOPは設定しません。

Windows media childはCREATE_NO_WINDOWを使用し、CreateProcessの短い区間だけ親のerror modeへSEM_FAILCRITICALERRORS／SEM_NOGPFAULTERRORBOX／SEM_NOOPENFILEERRORBOXを追加して継承させ、元へ戻します。frozenではPyInstallerのDLL directoryもその区間だけ標準検索へ戻して復元します。media spawn同士だけをmutexで同期し、実行中のFFmpeg processを直列化しません。DETACHED_PROCESSは使いません。pause／stop／timeout時の子process回収は既存CancellationTokenと共通化しました。

起動例外、stderr、return status、Windows codeを記録し、retry・timeout・input invalid・output I/O・fatal job/global・exhaustedを区別します。popup抑止に成功したと仮定してerrorを無視することはありません。UUID付きattempt tempだけをretry時に削除し、既存のvalidation→transactional publishと旧正常成果物の保護を維持します。

調査時の実ffmpeg／ffprobeは9.0.1で-version成功、依存DLLはWindows system/API setでした。該当Application eventと残存processは0。障害発生時のdesktop heap／handle／security干渉の観測がないため、OS初期化失敗の具体的誘因は未特定です。凍結環境のDLL検索継承は既知のリスクとして対策し、原因と断定していません。同時実行1/2の設定は固定変更しません。

参考: [Microsoft SetErrorMode](https://learn.microsoft.com/en-us/windows/win32/api/errhandlingapi/nf-errhandlingapi-seterrormode)、[PyInstaller external program subprocess](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html)。

## Job No

旧No列はset_jobsと部分更新の両方でrow+1を表示していました。sort後に更新したrowだけが再採番され、未更新rowの旧表示と衝突する原因になります。Job JSONにはjob_noがありませんでした。本Repositoryのproduction監査は16件、割当0・欠落16・既存persistent重複0でした。

Job IDとは別の正整数job_noを追加しました。system/job-number-sequence.jsonのnext_job_noをatomic保存し、process間counter lockとprocess内mutexで最初のJob保存まで排他します。予約後の保存失敗は欠番を許容し、削除した番号は再利用しません。同じIDの同時importやcallerによる番号の差替えも防止します。

startup migrationではcreated_at／job_id順に既存正整数の正本を保持し、重複の後続と欠落だけへmax以上の未使用番号を付けます。更新はraw envelopeのjob.job_noだけで、updated_atも変更しません。読込不能Job／未来schema／不正counterは黙って除外せずエラーにします。Notebook identity、checkpoint、各path、filenameは変更しません。GUIはsort・部分更新・再起動でもjob_noを表示します。

本番適用の前には全対象JSONのhashとbackupを保存し、適用後にJob countとjob_no以外の全fieldを照合します。Acceptanceは隔離rootとsynthetic mediaだけを使用します。

## Release Gate結果

Ver2.7の1064 testsを基準に38件を追加し、昇格前とVer2.8昇格後の両方で全1102 testsがPASSしました。追加は選択／採番17件、FFmpeg21件です。既存保存テスト5件はcounter予約を先に確定し、Job publishだけへ故障注入するよう分離しました。handle close・bounded retry・保存失敗時の原本保護に関するassertionは維持しています。

隔離した実GUI／実メディアAcceptanceとEXE 13系統がPASSしました。V28専用Acceptanceでは両GUIで10・11・12・13の4行全選択／解除を確認し、再起動後も番号を保持しました。Windows childの制御したNTSTATUS終了を使い、1失敗→retry成功と4失敗→対象Jobのみterminal→次Job完了を確認しました。自然発生のDLL初期化popupを再現した証拠とは区別します。実media spawnは27、peak2、終了後active0でした。

本番state38 filesをRepository外へhash一致backupした後、16 Jobへ番号1〜16を付与しました。次番号17、重複0、Job count不変、job_no以外の全field変更0です。Notebookや本番メディアの削除はありません。

portableとZIP展開後のportableもPASSしました。両GUI・設定の再起動、同梱media tools、browser、preset、Fake E2Eを確認しています。配布ツリーのuser data／production JSON／media／profile／credentials／logs／fixture混入は0です。最終distはVer2.8 folder・ZIP・SHAだけです。

旧Ver2.7の全fileとZIPのSHA／CRC一致、runtime data不在を確認してから削除しました。今回生成したbuild intermediate・検証コピー・synthetic fixtureもmanifest付きで整理し、25,611 files／13,636 directories、6,192,053,088 bytesを削除、失敗0でした。読み取り専用等4項目は保持し、属性・ACL・security policyの変更は行っていません。削除後の正式EXE schema smoke、成果物SHA、production field比較もPASSしました。詳細はwork/v28のXML・JSON・screenshots・cleanup manifestに保存しています。
