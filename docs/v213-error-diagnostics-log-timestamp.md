# Ver2.13 Error diagnostics / log timestamps

発生時刻、Job identity、工程、原因、復旧を妨げる条件、次の操作を追跡できるよう、eventの生成・永続化・GUI表示を統合します。

## 調査結果

旧`GuiPipelineController._runtime_update`はtimestampを生成せず、Job情報をruntime内へ残したままlevel/stage/messageだけをログcallbackへ渡していました。`LogDialog.append_record`はトップレベルのtimestamp/job_id/engine等だけを読むため、時刻・Job・Engineが空欄になりました。Download guardにあるUTC timestampもnested eventや文字列messageへ入り、時刻列へ届きませんでした。原因は主に発行時刻の欠落とフィールドmappingの不一致です。

旧GUIのメイン実行表示は`datetime.now()`を描画時に使っており、eventの発生時刻とは独立していました。実行ログdialogはmemory上のmodelのみで、調査した運用版のlogs directoryに永続event logはありませんでした。旧`job.result`は具体的errorを転送せず、`FATAL_FAILED`を単に「自動再開できないエラー」へ翻訳していました。

INVST038の原本をread-onlyで確認しました。保存Noは404、Ver2.12のerror codeは`NOTEBOOK_RESUME_METADATA_MISSING`、checkpointは`STOPPED:UPLOADING`、Notebook ID/URL、生成claim、生成開始時刻は未保存、Source状態はUNKNOWNです。`_recover_remote_job`の識別情報guardと、それをFATAL_FAILEDとして終了表示する経路に一致します。ここはPython例外を投げる処理ではありません。過去のtracebackもevent timestampも保存されていないため、元例外typeや画像の14:44:18との厳密な時刻照合は復元できません。

識別情報がないことから、実際にremote Notebookが作られていないと推測することはできません。原本のidentity/checkpointを変更せず、既存Notebookとの対応確認を案内します。二重作成を招く自動作成・再生成の変更は行いません。

## Eventと診断

`core/diagnostics.py`がevent_id、timezone-aware timestamp_utc/timestamp_local、level、job_id/job_no/job_title、engine、stage、event_code、messageを整えます。正本はUTC、表示はJSTの`YYYY-MM-DD HH:mm:ss`です。runtime operationには発生時に付与し、他のcallbackも受付時に整形します。前eventのruntimeをコピーした場合は新しい発生記録にし、Downloadのsystem eventへ前Jobの失敗情報を引き継ぎません。

Jobのstart/result/nextはoperation_idとrun_idで関連付けます。Jobを持たないeventはjob_id=null、GUIでは「システム」と表示します。旧timestampのISO offset/UTC・epochは変換し、HH:mmは日付を捏造せず保持します。naiveな旧日時にはTZ不明と表示し、時刻のない旧記録は「時刻不明（旧記録）」とします。

Error Detailsにはcode/category/stage、元exception type、安全なmessage、retryable、保存された試行回数、exhaustion、recovery block reason、next action、diagnostic ID、traceback referenceを持ちます。既存のretry/terminal判断を表示の都合で変更しません。識別情報不足では、ID/URL未保存・二重作成防止のため自動再開保留・対応確認という具体的な案内を表示します。

捕捉した例外は、元traceback、module/function/call pathとともに`logs/diagnostics/<diagnostic-id>.json`へ保存します。同じ例外messageが繰り返されても別IDで残し、旧recordにない例外を推測しません。localsは取得しません。Cookie、Authorization、OAuth/token/API key/password、query付きURL等を再帰的にredactし、GUI詳細でも再度適用します。

結果保存eventで元の失敗工程を上書きせず、選択行の詳細には人間向けmessage・原因・次の操作を先に表示します。状態保存/Job番号競合を隔離する境界では、業務checkpointを置き換えないshadow診断で元例外を保持します。保存待ちを別の業務エラーに書き換えず、診断IDから元の保存例外へ辿れます。

全eventは`logs/events.jsonl`へappend-onlyで保持します。同じdirectoryに対するprocess内mutexを共有します。ログ書込み障害がJob isolationを破壊しないよう、元exception情報をmemory側へ保持し、保存障害を明示します。別DBや既存ログの削除は導入していません。

## GUIと負荷

通常画面は人間向けsummaryと重要eventを表示し、内部job.start/result/next、capability/scheduler/downloadの生データはDEBUGとして保持します。「詳細ログを表示」は初期OFFで、ON時に保存済み記録も読み込みます。Job/Engine/Stage/Level/Messageの既存filterは維持し、選択行から診断ID・詳細・tracebackへ辿れます。

表示上限は最新5,000件。起動時はファイル末尾から必要な行だけを読み、全履歴をGUIへ展開しません。通常画面ではDEBUG floodのwidget更新を避け、詳細切替時に再読込します。両Phaseで同じlog dialog/modelとフィールドmappingを使います。

Stopにも独立した発生記録を作り、前のJob Errorのoutcomeを引き継ぎません。preflightやpipeline内のRunCancelledを通常の例外エラー通知へ流さず、実際のユーザー停止はUSER_STOPとして扱います。先行するJob Errorはログへ保持します。

## 検証と配布

基準1260件へ51件を追加。timestamp/legacy/JST、型混在、再起動保持、Job/operation対応、各error分類、redactionと再適用、元traceback保持、retry/Stop/isolation、両GUI、通常/詳細filter、大量eventの表示上限を検証します。2万eventのbounded model、通常表示でのDEBUG描画抑制も確認します。

sourceの実Qt GUI受入では、Jobとsystemの発生時刻、旧日時・時刻不明、例外詳細の参照、secretのmask、両Phase、再読込、2万eventの性能を検証します。全source/GUI Gate後、開発Version Ver2.12の事前EXEで同じ受入を通してから2.13.0 / 2.13.0.0 / Ver2.13へ更新します。正式EXEでは新suiteと既存17 suitesを再検証し、最小PATHのPortable、最終ZIP展開後、package auditを行います。

本番原本のJob・ログ・メディア・Notebook/Source/Artifactを変更・削除しません。試験はcopy/fixture rootへ限定し、配布にはJob、profile、credential、runtime、log/traceback/screenshot/fixture dataを含めません。証跡と54項目報告は開発端末の`work/v213/`へ保存します。

旧公開Ver2.12の再生成根拠: source `88270f2864285debfa5b27913ccbdb065a6fda44`、ZIP SHA-256 `e22a4f964b746f10501d117d5ba1df12837544fa9fdcf41d0c0e2ba33d977334`、EXE SHA-256 `07d6d0322b304f770b63282506c83c4666a3b81d9e0c12c22a11ae8a917a7af4`。再buildは当該sourceと保持するpackaging script/spec、config/build assets、検証済みFFmpeg/ffprobeを使用します。新配布の全Gate後、検証済み公開旧成果物と今回の生成copyのみcleanupし、Dropbox配備・利用中profile・ログ原本は保持します。

最終結果: source 1311 passed（317.33秒）、release 1311 passed（295.42秒）、追加51件、failure/error/skip 0。正式EXEは18/18 suites PASS、両Phaseの実GUIで元工程・cause・next action・JST日時・診断参照を確認しました。最小PATH・日本語/空白pathのPortable検証PASS。本番保護対象5351ファイルは変更0です。

正式ZIPは278,196,187 bytes、SHA-256 `d362c7d06c8ab8969df73f2652adffe2c1ffb48fb66335ee0bb188c89c989c6b`。EXE SHA-256は`a1579df992b426d5857fbb392a2a4afef482fb22e2e42ae52f139b128ff62923`です。Package auditは409 files、private/runtime user/fixture data 0。検証用のredactedな診断例を`work/v213/example-fixture-diagnostic.json`へ保持します。

最終ZIP展開後のPortable検証もPASS。旧公開Ver2.12はSHA/CRC・収録内容・空のruntimeを照合し、今回の生成build/copyとともに28,472 files / 7,933,701,780 bytesを削除しました。削除失敗・readonly skip・本番データ削除は0。長いWindows pathも同じworkspace containment検証のもとで処理し、OS policy/ACLは変更していません。Git履歴、source、build assets、checksums、検証結果・画像・redacted診断例は保持しています。
