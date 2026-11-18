# Ver2.12 Quota restart / browser profile recovery

保存済みQuotaの回復確認時刻を過ぎた後、現在のNotebook Studio UIで生成可否を再確認します。時刻経過だけで生成可能にせず、Chrome接続障害もCredit継続の証拠にはしません。

## 原因と実環境

旧`BrowserManager.start/prepare_for_processing`は、生きているcontextを再利用する前に`_wait_for_profile_unlock`を呼んでいました。既定probeはprofileのlockfile等のunlinkを試し、現に所有中のChromeによる共有違反を外部lockと同じ失敗にしていました。Quotaを保存した状態でStartすると`gui.app.DeferredPage`が最初のDOM参照時に再びpreflightへ入り、この順序の問題が発生し得ます。

旧schedulerは`BrowserProfileLocked`も一般の`limit.recheck.failed`として待機を延期し、接続状態・接続owner・現在のQuotaを区別しませんでした。実環境ではVer2.11の保存Quotaがactive、resume_atは2026-11-18 12:26 JSTで期限経過済み。専用profile使用中Chrome PID7860の親は稼働中のDJDmaker.exe PID16780でした。別の開発managerからは正当に接続済みのcontextではないため、attach/kill/lock削除は行っていません。当時のアプリ内context参照を過去ログだけから確定することはできないため、所有済み接続の再利用失敗と外部managerから見た生存owner待機を分けて再現しました。

## 接続と所有権

- 現在のowned contextが正常なら同じworkerで再利用し、profile probeや重複起動を行いません。異なるworkerからの使用は接続待ちとします。
- 接続がない場合は現在のChrome processのprofile引数を読み取りで照合します。Windowsの標準argument parserで日本語・空白・引用符を処理し、command line本文はログへ出しません。
- 接続済み、現在AUTH、所有process生存、他の生存owner、owner不明、ownerなしを区別します。生存ownerと判定不能の場合は新規起動しません。他processへのattach・kill、lock削除、profile内容変更は行いません。
- ownerなしのstale markerはChrome自身の既存処理に委ねます。日時だけでlockを削除しません。
- AUTH起動command、CDP起動方式・flag、Current Windowの判定方式は変更していません。過去のopen/close履歴はowner判定に使いません。

## Credit状態とscheduler

`credit_state`でCREDIT_LIMIT_ACTIVE / CREDIT_RECHECK_DUE / CREDIT_RECHECK_IN_PROGRESS / CREDIT_RECOVERED / CREDIT_RECHECK_BROWSER_BUSY / CREDIT_RECHECK_TRANSIENT_ERROR / AUTH_REQUIRED / UNKNOWN_UI_NEEDS_REVIEWを区別します。旧documentの読込互換は維持し、中断されたIN_PROGRESSも再起動時にDUEへ戻します。

resume_atは現在UIを確認する期限です。Browser失敗は最終Quota観測を保持し、15→30→60→120秒（上限120秒）の接続再試行へ移ります。確認不能なUIは利用可能と推測せず別理由で待機します。現在の「後で生成」のみを確認した場合は600秒後のCredit Probe、有効な「生成」を確認した場合だけ解除します。両ボタンがあれば有効な「生成」を優先します。

既存の生成前Notebookを優先し、該当候補がない場合はSource READYの完了済みNotebookを読み取り専用の確認先として使用できます。既存artifactを生成候補へ戻さず、Job checkpoint・preset・生成claimを書き換えません。hydrationをboundedに観測し、正しい動画設定form内の生成buttonを分類して閉じます。閉じた後のtooltipによる一時的な遮蔽も、強制clickやEscapeではなくboundedなactionability再確認で扱います。

期限到達した再確認は新しいLocal処理より優先します。実行中FFmpeg・Downloadを中断せず、安全な境界で行います。Browser待機中はLocalを継続し、Browser依存候補を実行可能と数えてbusy loopしません。接続回復・Credit解除後は未生成jobを再び生成優先へ戻します。再確認待ちをJob FAILEDやUnknown ModalのHuman Waitにはしません。

## 検証と制限

追加41件で、09:00 Limit→12:26期限→11:00 Stop→13:00 Restart、時刻経過だけでは解除しないこと、reason別backoff、接続再利用、foreign/unknown owner保護、Local継続、Download defer、busy loop防止、完了Notebookでの読み取り専用確認、両GUI表示を検証します。旧Ver2.10の一律600秒・認証例外の期待値は、今回の理由別再試行・AUTH_REQUIREDへ更新しています。

最小PATHのPortable検証でowner照会のPowerShell探索依存を検出しました。Windows標準の`System32/WindowsPowerShell/v1.0/powershell.exe`をSystemRootから絶対pathで指定し、Python・Chrome・PowerShellをPATHに登録しない状態でも照会が実行できる試験を追加しました。通常PATHでの成功をPortable成功の代用にせず、修正後に全試験・build・配布検証を再実行します。

実機確認は、稼働中Ver2.11のChromeに接続せず、ownerなしを確認した既存Ver2.10専用profileをBrowserManagerで起動して実施しました。現在Notebook host、同一context/PID再利用、現在Studioの有効な「生成」、設定画面close、期限切れQuotaのコピーの解除を確認しました。実生成・Source送信・新規Notebook作成・remote削除は0。自然なLimit→Recovery遷移を待った試験とは区別します。

source/EXE smokeでは別managerが所有する実Chromeによるlock、所有managerによる正常終了、接続回復、現在UIでLimit継続→600秒、利用可能→schedulerからfixture内で1回だけ生成、両GUI・fatal dialog 0を検証します。既存16 EXE suitesも維持します。Chromeのnative復元通知は強制再現せず、既存の無害な通知surrogateで非操作を確認します。

稼働中の本番アプリ自身によるQuota/Job更新は、開発側の書込みと区別してauditへ記録します。本作業の試験保存先はwork内のcopy/fixtureです。配布物には本番JSON、profile、Cookie、credential、メディア、runtime state、log、fixture dataを含めません。

旧公開Ver2.11の再生成根拠: source `49db00c3b4379f34b4b7ae0961fc4322f174eaf0`、ZIP SHA-256 `42e99022058984e661855ca0cb8393ff77b578315b9558e0307051ecde1fdc62`、EXE SHA-256 `e60e0e14045ab7e79a188f37aad51b204d1455c520962d5e7dadf5464eddd7ad`。再buildには当該sourceと保持するpackaging script/spec、config、build assets、検証済みFFmpeg/ffprobeを使用します。新配布Gate通過後、公開distと今回の生成copyだけを検証してcleanupし、Dropbox配備と利用中profile・ユーザーデータは保持します。

最終結果: source 1260 passed（284.51秒）、release 1260 passed（308.30秒）、追加41件、失敗・error・skip 0。EXEは新しい復旧6checksと既存16 suitesの合計17/17 PASS。最小PATH・日本語/空白pathのPortable検証PASS。Package auditは409 files、private/runtime user/fixture data 0です。

Ver2.12 ZIPは278,171,221 bytes、SHA-256 `e22a4f964b746f10501d117d5ba1df12837544fa9fdcf41d0c0e2ba33d977334`。EXE SHA-256は`07d6d0322b304f770b63282506c83c4666a3b81d9e0c12c22a11ae8a917a7af4`です。詳細な試験・監査・cleanup・Git結果は開発端末の`work/v212/`に保存します。

最終ZIP展開後のPortable再検証もPASSしました。旧公開Ver2.11と今回生成したbuild/copyをCRC・SHA・path containment・tracked file保護で確認し、34,346 files / 5,714,801,674 bytesを削除、失敗・readonly skipは0。source/history/checksum/build assetsとDropbox配備は保持しました。監査対象4,361ファイルのうち本番Ver2.11による外部更新は8ファイル（Quotaと3 Jobのbackupを含む）で、追加0、JSON解析エラー0、その他変更0。本作業の本番JSON書込み・メディア/remote削除は0です。
