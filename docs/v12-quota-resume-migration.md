# Ver1.2候補：作業記録・現行DOM確認待ち

指示ID: `DJD-CHAPPY-V12-QUOTA-RESUME-JOB-MIGRATION-MERGED-FULL-002`

本書は初期候補時点の作業履歴。以下の保留・BLOCKED記録は当時の判断であり、最新のrelease判断は[v12-final-release-gate.md](v12-final-release-gate.md)を参照。

## 最新のRelease Gate補正

指示`DJD-CHAPPY-V12-FINAL-RELEASE-GATE-CONTINUE-FULL-001`により、
`SOURCE_UPLOAD_ERROR_LIVE_RECOVERY: UNVERIFIED_LIVE`を正式な既知事項として許容する。
upload完了はユーザー確認済みだが、自動復旧／手動再Start由来かは不明、live retry成功証拠はない。
安全性fixtureを必須とし、将来自然再発時の追加Acceptance対象にする。この未確認だけをrelease blockerにしない。
以下の従来記録でsource live再試行を必須としている箇所は、この補正を優先する。
継続作業の最新結果は[v12-final-release-gate.md](v12-final-release-gate.md)を参照。

## 元GNB一次調査

Repository: `C:\xampp\htdocs\PHP\AutoGeminiNoteBookCreator`
HEAD: `28bd51dfe2894018bfc9d65a02f219a933199127`。

- `app/automation/source_monitor.py`: filename登録、busyなし、source count、操作可能性の複合条件を2秒安定監視。error検出あり。
- `app/automation/notebooklm_page.py`: prepare/readback/send、送信確認60秒、応答開始120秒、応答完了180秒。
- `app/automation/notebook_processor.py`: prompt最大3attempt、再送前の履歴確認、sourceなし返信時はsourceを再監視。既存生成があれば再送停止。
- `app/automation/video_monitor.py`: 2秒poll、Studio空表示、生成中・予約・完成を分離。
- `app/config/selectors.py`, `app/config/defaults.py`: query-box selector、送信button、source timeout。
- `tests/test_source_monitor.py`, `tests/test_automation.py`, `tests/test_batch_processor.py`: source ready、prompt retry、既存生成の重複防止。
- `diagnostics/20260905_094202/STATE_05_SOURCE_READY/page.html`: `chat-panel`、`.source-panel`、中央の`textarea.query-box-input`と左検索欄の区別。
- `diagnostics/20260905_112246/STATE_08_VIDEO_CREATE/page.html`: `.from-user-message-card-content .message-text-content`と`.to-user-message-card-content .message-text-content`。
- README、開発ルール、source retry関連語、`git log --all -S 'source retry' -- app tests`も確認。

難読Angular classは使用していない。上記保存済みDOMは現在のGoogle側DOMを保証するものではない。

### エスカレーション

#### ユーザー追記：ソース読込完了の経緯は不明（2026-09-07）

ユーザー再確認ではアップロード完了。ただし、その後に誤って「授業動画生成開始」を押した際の再アップロードで完了した可能性があるとの申告であり、再アップロードの実行有無・完了に至った原因はいずれも**不明**とする。自然復旧、既存版の再試行成功、今回修正による復旧のいずれとも断定しない。

添付画像では、一方に`FAILED_TAX087_`の赤いsourceカード・0個のソース表示、他方に`HT087_`のチェック付きsource・1件のソース表示がある。Notebook名が異なり、同一Notebook／同一sourceの復旧前後を証明するものではない。後者の左側にはリサーチ結果も表示されているが、その発生経緯も画像だけでは断定しない。

この事例は原因不明として保留し、source retry実機PASSの根拠には使用しない。現在のエラーNotebook URLを繰り返し要求しない。再発時に対象Notebook ID・操作時刻・該当ログ・エラーDOMを照合するまでは不明扱いを維持する。今回の追記で実装・release判定は変更しない。

実現したい機能は「source error cardがある既存Notebookで、正常sourceを重複投入せずupload工程だけ再試行」。元GNBの再監視は見つかったが、エラーカードからのupload再実行操作は未特定。候補実装はsource panel内の明示Retry buttonのみを許可し、確認できなければNotebook/sourceを保持して停止する。

`C:\Users\Ichiro\Desktop\HTMLソースサンプル.txt`を受領・確認済み（422,024 bytes）。初期HTMLであり、scriptを除いたDOMにはsource-panel/chat-panel/textareaが0件、ソースuploadエラー本文も0件だった。記載されたNotebookを既存BrowserManagerで読み取り確認したところ、source 1件、busyなし、source error表示0、retry button 0、chat-panel 1、artifact 0だった。エラーUIの実物を確認するまでsource削除・再投入方式は確定しない。サンプル原本はアカウント関連bootstrap情報を含むためGitへ取り込まず、構造だけを診断に使用する。

サンプルNotebookの現行DOMで`.single-source-container`とsource名の完全一致aria-labelを確認。これを使ってerror判定とRetry操作を対象sourceだけに限定した。同名sourceが複数なら停止し、別sourceのエラーで正常sourceを再uploadしない。aria-describedbyで関連付けられたtooltip本文も検査する。対象分離・Retry欠落・重複・tooltipを含む追加5件PASS。ただし現行Googleのエラーカード上でのRetry操作は未検証。

## 実運用データ確認

実行中EXE:
`C:\Users\Ichiro\Dropbox\プロジェクト\個別アプリ開発\DJDmaker_Ver1.1\DJDmaker.exe`

実system/jobsは175件（完成75、失敗100）。失敗の内訳:

- `PRESET_APPLY_MISMATCH`: 93件、sent preset messageのDOM未確認。
- `NOTEBOOK_STAGE_FAILED`: 7件、自動生成開始未確認。
- 失敗100件はすべてNotebook URLあり。JSONだけからquotaとは断定しない。

実データコピー:
`work/v12-real-migration/system`

移行前バックアップ:
`work/v12-real-migration/system/migration-backups/resume-v2`

コピーの移行結果: 175件維持、完成75維持、失敗100維持。補助分類はPRESET_SEND_FAILED 93、RECOVERY_PENDING 7。既存metadataは追加2項目以外一致。原本175 JSONとbackupのSHA差異0。2回目の移行0件。所要21.343秒（保存＋全件再読込を含む）。Notebook作成0、source upload0、動画生成0。

実ユーザーフォルダのmigration、TXT移動、job削除、remoteへの送信・生成・削除は実施していない。下記の読み取り診断だけを実施した。

## 変更の実装状態

- `adapters/replies.py`: quota意味群＋score、否定除外、生成開始分類、相対reset、時刻優先・矛盾検出。
- `adapters/chat_flow.py`: 中央Chat限定、全文readback、send有効待機、DOM順序による今回user/reply相関、180秒待機、3attempt、遅延reply再確認。
- `adapters/notebook.py`: source error検出・上限3回、既存source再利用、作成直後のNotebook identity永続化、既存Notebook診断、既存reservation経路呼出。
- `core/models.py`, `core/job_migration.py`: schema補助version、原因分類、移行前JSON backup、冪等移行。
- `orchestration/pipeline.py`: 同じjob ID/snapshotでcheckpoint再開、完成jobを診断・再生成対象外。
- `core/completed_txt.py`: 完成TXT保存先移動、hash照合、衝突保護、失敗補助状態、process内直列化。
- `core/repositories.py`: 完成のみ選択削除、queue参照除去、成果物保持、再読込復活防止JSON。
- `gui/main_window.py`: 白GUIの選択checkbox・削除2button・全dataset自然順sort、job ID選択維持、RecoveryのEnding未設定による不活性を解除。実行時のEnding案内は維持。

既存のNotebook再作成を期待するテストと古いquota precheck期待は、今回の上位仕様に合わせて同一Notebook再試行／今回返信判定へ更新。テストの削除やskipで通過させていない。

## 検証

全体回帰: 353 passed（最終全体実行167.66秒）。`python -m pytest -q --basetemp work/v12-html-final-regression --tb=short`で今回の全変更を一括検証。compileall、git diff --checkもPASS。ISO日時のtimezone部分を別のHH:mmと誤認しない補正と、不正ISOから相対時間へのfallbackも回帰確認。
実ChromeのローカルHTML fixtureで中央Chat限定、source検索除外、disabled送信禁止、送信3回上限、古いreply除外、遅延replyの二重送信防止を確認。Googleへのlive送信を意味しない。

### 現行Google Notebookの読み取り診断

元実装互換`BrowserManager.prepare_for_processing`で既存専用profileのsessionを利用し、各群の代表1件ずつを読み取り確認。診断が起動したautomation contextは終了した。

- 生成開始未確認群の代表: artifact NOT_STARTED、source READY、preset SENT、reply QUOTA_EXHAUSTED、user/assistant各1件、保存snapshotと履歴完全一致。
- 送信確認失敗群の代表: artifact NOT_STARTED、source READY、preset NOT_SENT、reply NO_RESPONSE、user/assistant各0件。
- 両方で中央Chat inputの厳格scopingに成功。全文・Cookie等は通常logへ保存していない。
- 続いて失敗100件全件を読み取り診断した。source READY 100、artifact NOT_STARTED 100。旧分類器ではQUOTA_EXHAUSTED 6、NO_RESPONSE 93、OTHER_FAILURE 1。診断結果はGit対象外の`work/v12-failed-remote-audit.json`に保存。
- OTHER_FAILUREの1件を読み取り再確認すると、「説明動画のリソース（クォータ）が不足し、約2時間後に回復するため生成開始できない」という返信だった。意味条件は満たすがscore 4で閾値未達だったため、仕様どおり生成失敗・相対回復時間の加点を実装。観測した文面による回帰テストでQUOTA_EXHAUSTED、reset +2時間を確認した。短い動画への勝手な切替は行わない。したがって原因内訳は未送信93、クォータ返信7。全100件の診断JSONは修正前の観測値のまま保持している。
- 新Preset送信、予約操作、生成、Notebook/source/artifact削除は0件。今回の読み取り診断はlive送信・予約のAcceptance合格を意味しない。

## 引継ぎの確定範囲

旧runtimeの場所とsystem保存形式、コピー上の自動schema移行は確認済み。新portableをまだ作っていないため、実ユーザー向けの最終手順は未確定。

予定手順は、Ver1.1を安全終了→system全体をbackup→検証済みVer1.2を別folderへ展開→systemを一括copy→新起動時のmigration→175/75/100件を照合。既存settingsのinput/raw/output/Endingは絶対pathなので引き継がれる。browser profileはChrome終了とlock解放を確認してから引き継ぐ必要がある。実移行確認までは旧Ver1.1を削除しない。RAW/ZIP等の大容量copyはmigration処理には含めない。

## Release残件

1. 現行source error cardの安全なretry方式の確定（HTML受領・対象Notebookの正常状態確認は完了）。
2. 現行中央Chat/reply DOMの読み取り適合は確認済み。source再試行・新規送信・返信待機・予約の実動作確認は残る。
3. 全要求の追加異常系・checkpoint回帰網羅の最終確認。
4. 全Gate通過後のVersion 1.2、正式portable、展開試験、実移行案内確定。
5. その後にlocal commit、main通常push、HEAD一致確認。

main基準は`a8c16b4325e4e1f4c591a15a3c90f8ade06e0a46`。phase2は`46c4599e76383b663552aec3f052e0dca8cc09f9`のまま。phase3は作成していない。

DJDMAKER_V12_QUOTA_RESUME_MIGRATION_RESULT: BLOCKED

## 指定57項目の報告

返答ID: `DJD-CODEX-V12-HTML-AUDIT-20260907-002`

| 項目 | 結果 |
|---|---|
| 1 repository | C:\xampp\htdocs\PHP\DJDmaker |
| 2 branch | main |
| 3 HEAD before | a8c16b4325e4e1f4c591a15a3c90f8ade06e0a46 |
| 4 origin/main before | 同上 |
| 5 FAILED分類 | 全100件live読取、未送信93／クォータ返信7。後者1件のscore不足を修正 |
| 6 source failure detector | 対象cardの本文/title/aria-label/aria-describedby。別source分離・重複拒否を含むDOM試験PASS |
| 7 source retry | 最大3、既存Notebook保持。現行error card操作未確定 |
| 8 source ready | 既存複合条件＋2秒安定、失敗100件と提供サンプルNotebookは全件READY |
| 9 chat target | 中央chat-panel限定。全100件のlive読取診断完了 |
| 10 source search除外 | 中央container外を拒否。ローカルDOM PASS |
| 11 send active待機 | visible/enabled待機。ローカルDOM PASS |
| 12 send確認 | 今回user messageを照合。ローカルDOM PASS、live送信は未実施 |
| 13 reply wait | 元GNB基準180秒、2秒poll |
| 14 retry3回 | ローカルDOMで最大3回を確認 |
| 15 reply correlation | DOM順序＋送信前履歴prefix、古い返信除外 |
| 16 quota classifier | 意味群＋score＋否定除外。実返信のresource/quota不足を追加回帰確認 |
| 17 pattern1 | 単体test PASS |
| 18 pattern2 | 単体test PASS、145分reset |
| 19 pattern3 | 単体test PASS、143分reset |
| 20 relative reset | timezone付き、明示時刻優先、矛盾検出 |
| 21 generation accepted | 動画＋作成/生成＋開始の文脈判定、test PASS |
| 22 old reply除外 | 同一presetの過去返信を今回返信に再利用しない。DOM test PASS |
| 23 human re-Start | artifact診断後、未開始なら同一jobで今回preset再送へ |
| 24 current quota→reservation | 既存reservation関数を使用、現在liveでの予約操作は未実施 |
| 25 Recovery button | Ending未設定のみを理由に不活性化しない。実行時に案内 |
| 26 Recovery対象 | 既存予約/生成後pending。未生成quotaは通常Start側 |
| 27 TXT完成時move | 実装・fixture PASS |
| 28 Start時move補完 | startupとStartに接続。実データ移動未実施 |
| 29 TXT collision | 同名異内容を上書きしない。test PASS |
| 30 checkbox | job IDへ紐付け |
| 31 selected delete | COMPLETEDだけ。未完了混入拒否 |
| 32 completed delete | 全完成対象＋確認dialog |
| 33 artifacts保持 | record/queueのみ除去。再読込復活防止記録あり |
| 34 sort | 全dataset、全column、ASC/DESC |
| 35 natural sort | TAX2 < TAX10、175件fixture PASS |
| 36 selection保持 | sort/再描画後のjob ID選択保持PASS |
| 37 migration | 実データcopy175件PASS |
| 38 backup | system JSONのみ、元bytesのSHA一致 |
| 39 175件相当 | fixtureと実copyの両方を確認 |
| 40 completed before/after | 75→75 |
| 41 failed before/after | 100→100。初期補助分類93 preset失敗/7 remote要診断。追加読取で未送信93／クォータ返信7と確認、実job JSON変更なし |
| 42 completed再生成 | 0、実copyでは生成関数を呼ばない |
| 43 Notebook再作成 | live診断・実copyとも0 |
| 44 source再upload | live診断・実copyとも0 |
| 45 tests | 全353 PASS、167.66秒。compileall／diff check PASS |
| 46 performance | 実copy migration＋再読込21.343秒。GUI175件sort test PASS |
| 47 portable | 未build、release blocker解消後 |
| 48 実移行方法 | runtime所在・copy移行確認済み、正式portable手順は未確定 |
| 49 GUI変更 | 白GUIのcheckbox/削除/sort/Recovery活性条件のみ |
| 50 phase2変更 | 0、ref 46c4599e76383b663552aec3f052e0dca8cc09f9 |
| 51 Version | 1.1.0維持。PASS未達で1.2扱いにしない |
| 52 cleanup | 新buildなし。旧Ver1.1とユーザーデータを保持 |
| 53 commit | なし、main working treeに候補変更あり |
| 54 push | なし |
| 55 HEAD/origin after | 両方a8c16b4325e4e1f4c591a15a3c90f8ade06e0a46 |
| 56 ahead/behind | 0/0（未commit変更は別） |
| 57 未解決 | source error card再試行方式、live送信/予約、網羅確認、portable/実移行/release |
