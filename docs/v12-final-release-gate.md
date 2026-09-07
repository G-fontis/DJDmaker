# Ver1.2 Final Release Gate 継続記録

指示ID: `DJD-CHAPPY-V12-FINAL-RELEASE-GATE-CONTINUE-FULL-001`

main / HEAD before: `a8c16b4325e4e1f4c591a15a3c90f8ade06e0a46`

## 既知事項の扱い

`SOURCE_UPLOAD_ERROR_LIVE_RECOVERY: UNVERIFIED_LIVE`

ユーザーによるupload完了確認はあるが、その原因・再uploadの有無は不明。
live修正成功とは報告しない。fixtureで安全性を検証し、自然再発時に追加Acceptanceを行う。
この既知事項単独ではVer1.2をBLOCKEDにしない。

## 今回の確認・修正

- 隔離profileへのユーザーログイン後、Pre-flight PASS。未送信代表1件と旧quota代表1件を既存Notebook上で実送信した。両方source READY、再uploadなし、snapshot全文readback、中央Chatの送信有効化・今回user message・今回replyを確認。両方GENERATION_ACCEPTEDで予約なし、各送信1回。旧quotaを現在不足として扱わないことをlive確認した。
- Live job ID: 未送信`46b7a92cdbd445cab0f5304ae1ccbf4b`、旧quota`3d17929660f64dfdac0d7f0a85b4a2b4`。既存remoteに各1回生成依頼した。コピー側のみWAITING_VIDEOを保存。本番job JSONは変更しないため、本番で再開する際もremote状態を先に診断する必要がある。
- 全93件／7件をliveで再送したわけではない。全数の制御は175件fixture、liveは代表各1件。現在quota不足→予約の分岐はfixtureで確認し、今回liveでは発生せず未実行。quotaを人工的に消費して再現しない。
- 175件fixtureで完成75件の全metadata不変、残り93件＋7件の同一job ID／preset snapshot再開を確認。
- 7件の旧quotaは現在のsubmitを実行し、現在成功ならWAITING_VIDEO、現在quotaなら既存予約経路のSCHEDULED_REMOTE／RESERVED_WAITING_CREDIT_RESETへ。いずれの分岐もfixtureで確認。
- Recoveryの4対象状態それぞれについてbutton有効化と、新規submitをしない回収cycleを確認。
- source errorが継続するfixtureで対象cardだけ最大3回retry、source重複投入0、Notebook再作成0を確認。
- 返信なし3回後の例外が汎用NOTEBOOK_STAGE_FAILEDへ置換される不具合を追加テストで再現。具体的なPRESET_RESPONSE_TIMEOUT等を保存するよう補正。
- 本番profileは操作しない。既存の隔離Acceptance profileを確認したがBrowserAuthenticationRequiredであり、live再開には人間によるGoogle認証が必要。
- 実175件を新規`work/v12-migration-final-42d58yq_`へコピーして最終migrationを実行。COMPLETED75／FAILED100／job ID175維持、既存metadata保持、原本system JSON変更0。2回目migration0、migrationと再読込等は30.616秒。remote・media操作0。
- 隔離profile `work/critical-preset-live-portable/browser/chrome-profile`で、既存BrowserManagerと同じ通常Chrome起動関数を使いAUTH Chromeを開いた。remote debugging／CDP／automation flagsなし。認証後にChromeを閉じてから同じ試験profileでliveを継続する。

## Ver1.1→Ver1.2移行手順（portable構成・コピー試験確認済み）

`dist/DJDmaker_Ver1.2`と配布ZIPを作成し、日本語＋空白pathの展開先でEXEの175件migrationを確認済み。cleanup保持例外は承認済み。本番移行はGit反映を含むrelease完了確認後に行う。

1. Ver1.1と、その専用Chromeを完全終了する。現在の`C:\Users\Ichiro\Dropbox\プロジェクト\個別アプリ開発\DJDmaker_Ver1.1`は削除しない。
2. 旧folderの`system`全体と`browser\chrome-profile`をローカルの非公開backup先へ保存する。profileには認証情報があるためGit・配布ZIPへ入れない。
3. 正式承認後の`DJDmaker_Ver1.2`を旧folderとは別の書込可能なfolderへ展開する。旧folderへ上書き展開しない。
4. 新版初回起動前に旧`system`全体を新folderへコピーする。`jobs`だけでなくsettings、presets、queue等のJSONもまとめて保持し、既存job IDを変更しない。
5. Chromeが終了してprofile lockが解放済みであることを確認してから、旧`browser\chrome-profile`を新版の同じ相対位置へコピーする。認証を再利用できなければ通常のGoogleログインを行う。Cookieを個別編集しない。
6. RAW／ZIP／HLSの既存保存先を維持する。実settingsのinput/raw/output/Endingは絶対pathなので、その参照先を移動・削除しない。大容量mediaのmigrationコピーは不要。
7. 新版を起動すると`system/migration-backups/resume-v2`へ移行前JSONを保存し、補助schemaを更新する。再起動しても二重migrationしない。現候補は起動時にも完成TXT移動の補完を行うため、実データの初回起動は本番移行承認後に限る。試験時はsettingsだけでなくjobのsource/media参照先も隔離し、本番の絶対pathを使わない。
8. 初回確認ではStart前に総数175／COMPLETED75／FAILED100とjob IDを確認する。原因補助分類は初期metadata分類とremote再診断後の分類を区別する。
9. preset選択は起動時未選択。既存jobの本文snapshotは保持される。必要なpresetを選択してStartすると未完了のみstate-aware resumeし、完成75件は再生成しない。完成TXTの移動補完は行われる場合がある。
10. Recoveryは生成済み・予約済み・未回収のチェック専用。生成前の失敗93件／7件の再試行はStartを使う。移行確認が完了するまで旧Ver1.1を保持し、旧版と新版を同時処理させない。

誤検出を避けるため旧runtime自動探索・自動importは追加しない。上記の明示コピー方式を採用する。コピー試験では本番pathを隔離pathへ置換し、原本変更0を確認した。

## Portable・最終検証

全363 tests PASS（209.10秒）。compileall、git diff --check PASS。
cleanup保持承認後にも全363 testsを再実行しPASS（169.70秒）。EXE／ZIP SHA再計算一致、runtime file 0、commit対象のDB/media/profile混入0、機密鍵パターン検査該当0を再確認。ソース・build内容の変更はなく、再buildは行っていない。
既存PyInstaller onedir方式でVersion 1.2.0 / FileVersion 1.2.0.0 / GUI Ver1.2を確認。
旧Windows固定version tupleが1.0.0.0だった点も1.2.0.0へ整合し、回帰assertを追加した。

| 成果物 | size (bytes) | SHA-256 |
|---|---:|---|
| dist/DJDmaker_Ver1.2/DJDmaker.exe | 3,837,248 | 7F0FFC7E6719F6DB93DC484566AB7F636C75BE5392346F9DB579514235560837 |
| dist/DJDmaker_Ver1.2.zip | 277,615,541 | D7727920988FC6ECE5255B0140D68FBCB59E3FACC7238FE14E57FF72A06FDB7F |

未使用配布folderの408ファイルを監査。browser認証ファイル・runtime job・RAW/HLS/output ZIP・logsは0件。
Playwrightの4個の`.d.ts`はTypeScript型定義でありHLS segmentではない。ZIPのtestzip PASS。
展開先`work/v12 配布検証 9cn1t8xe/DJDmaker_Ver1.2`で実EXE起動・正常終了、settings保存／別process再起動復元、browser smoke、preset選択リセット、Fake E2E COMPLETED、FFmpeg/ffprobe、RAW/Ending/HLS/ZIPを確認。
別展開先`work/v12 移行検証 874bat5w/DJDmaker_Ver1.2`で、旧JSON175件をEXE起動時にmigration。完成75／失敗100／ID・snapshot・Notebook identity全保持、backup175件、原本変更0。
配布folderではGUIやAcceptanceを実行していないため、上記の試験データはZIPに混入しない。

## Cleanup保持例外（今回限り・ユーザー承認済み）

リポジトリ内の旧`dist/DJDmaker_Ver1.1`と`dist/DJDmaker_Ver1.1.zip`は、既報SHA一致・runtime file 0を確認。合計972,985,133 bytes。
ただし明示的に検証した対象への削除コマンドが実行環境ポリシーで拒否された。削除実行0、別手段での回避なし。
この旧配布物は保存済みsource・build手順・検証済み依存物から再生成可能なbinary／配布ZIPであり、ユーザーデータではない。2026-09-07、ユーザーが今回のみ保持する例外を明示承認した。cleanupを試みた事実と拒否理由を残し、今回は保持する。今後のcleanup義務を恒久的に解除するものではない。
現在dist 2,925,129,949 bytes、build 13,211,418 bytes。Phase2配布物は変更禁止対象として保持。Dropboxの本番Ver1.1と認証profile・ユーザーデータも保持。
保持例外によりcleanupのrelease blockerは解消。その他のRelease Gateを再確認してVer1.2をcommitし、`https://github.com/G-fontis/DJDmaker.git`のmainへ通常pushする。force/rebase/reset/amendは禁止。Git反映の実測SHA・同期・clean確認はcommit後の最終報告で提示する（本書は当該commitに含む事前検証記録）。

## 指定41項目・release commit前の検証と承認

返答ID: `DJD-CODEX-V12-FINAL-RELEASE-20260907-005`

| 項目 | 結果 |
|---|---|
| 1 HEAD before | a8c16b4325e4e1f4c591a15a3c90f8ade06e0a46 |
| 2 175 migration | 新規copyで175件PASS |
| 3 Completed before/after | 75/75 |
| 4 Failed before/after | 100/100 |
| 5 job ID保持 | 175件全保持 |
| 6 Preset93再開 | 同一ID/snapshot全数fixture PASS、live代表1件GENERATION_ACCEPTED |
| 7 Quota7再開 | 両分岐全数fixture PASS、live代表1件の現在再送GENERATION_ACCEPTED |
| 8 current retry | 現在のsubmitを必ず通すfixture PASS |
| 9 old reply除外 | 中央Chatの送信前履歴prefixとDOM順序で相関、回帰test |
| 10 reply classification | 意味群・score・否定除外・相対回復時間 |
| 11 retry3回 | 返信なし最大3回、遅延reply再確認、固有timeoutコード保持 |
| 12 reservation | 現在不足時の既存経路接続test PASS。今回live2件は生成受付のため予約しない |
| 13 Recovery button | 4状態の有効化／submit禁止fixture PASS |
| 14 TXT move | 完成時移動の既存回帰test |
| 15 TXT補完 | startup/Startの補完、試験は隔離pathのみ |
| 16 TXT collision | hash不一致は上書き禁止、同一内容とretry回帰test |
| 17 checkbox | job ID紐付けの回帰test |
| 18 selected delete | 完成のみ、未完了混入拒否の回帰test |
| 19 completed delete | 全完成削除・確認UI、回帰test |
| 20 artifacts保持 | job recordのみ削除、media/remote保持の回帰test |
| 21 sort | 全column ASC/DESC、175件fixture |
| 22 natural sort | TAX2 < TAX10、選択ID維持 |
| 23 175件performance | 実copy migration等30.616秒、resume fixtureは10秒未満をassert |
| 24 source upload fixture | 対象分離・最大3回・重複防止・Notebook保持を追加検証 |
| 25 SOURCE_UPLOAD_ERROR_LIVE_RECOVERY | UNVERIFIED_LIVE（許容既知事項） |
| 26 migration backup | 新規copyのsystem/migration-backups/resume-v2 |
| 27 idempotent | 2回目0件 |
| 28 tests | 全363 passed。保持承認後の再実行169.70秒、compileall／git diff --check PASS |
| 29 live範囲結果 | 隔離profile認証・Pre-flight PASS。代表2件とも新reply生成受付、各送信1回 |
| 30 portable | build／展開GUI／settings／browser／Fake E2E／175 migration PASS |
| 31 package監査 | private/runtime media 0、ZIP integrity PASS、SHA確定 |
| 32 移行手順 | 上記明示コピー方式、EXE起動migration確認済み。本番実行はrelease承認後 |
| 33 Version | Python/Product 1.2.0、FileVersion 1.2.0.0、GUI Ver1.2 |
| 34 build cleanup | 試行を環境ポリシー拒否。再生成可能・非ユーザーデータを確認、今回限りの保持をユーザー承認 |
| 35 commit | 本書を含めrelease: DJDmaker Ver1.2として固定。実測SHAは最終報告 |
| 36 push | 承認済み手順: git push origin main（通常pushのみ） |
| 37 HEAD/origin after | push後に同一SHAを確認し最終報告 |
| 38 ahead/behind | commit前0/0。push後0/0・Git cleanを必須確認 |
| 39 phase2変更0 | ref 46c4599e76383b663552aec3f052e0dca8cc09f9維持 |
| 40 未解決事項 | source error liveは許容既知事項。cleanupは今回限りの承認済み例外 |
| 41 release readiness | 動作・配布検証PASS、例外承認済み。通常commit/pushの実測確認で完了 |

`LOCAL_RELEASE_GATES: PASS`

`SOURCE_UPLOAD_ERROR_LIVE_RECOVERY: UNVERIFIED_LIVE`

最終`DJDMAKER_V12_FINAL_RELEASE_GATE_RESULT`は、通常push後のHEAD一致・ahead/behind 0/0・Git cleanを実測したうえで最終報告する。
