# Ver2.7 schema修正と容量整理

## schema_version二重指定

原因はcore/cloud_limit.pyのCloudLimitGate._saveです。保存済みcloud_limit JSONを再読込すると、schema_versionとkindもstateへ入りました。期限後のPipelineCoordinator._refresh_capabilities→CloudLimitGate.defer_recheck→_saveで、明示keywordと**stateの両方から同じキーが渡されてTypeErrorになっていました。Job固有の破損ではなく、保存済みquota stateを再読込して再確認を延期する経路の問題です。

現行schema=1でも再現し、legacy限定ではありません。Git履歴のf2dced0（Ver1.2.5）で導入された式で、Ver2.6のRAW変更による混入ではありません。隔離した再現入力とstackはwork/v27/schema-repro-before.jsonへ保存しました。Project直下のproduction cloud-limit.jsonは存在しませんでしたが、本番Job JSON 16件はschema=1で正常に読めました。原本を書き換えた再現は行っていません。

CloudLimit専用のdocument validationでschema欠落・旧値0をcurrent=1へメモリ上で移行します。loadだけでは原本を書き換えず、次の正常saveでcurrentを保存します。domain stateからschema_version/kindを分離し、saveはdictのコピーをvalidationへ渡します。currentより新しい値や不正な型、異なるkindは明確なcompatibility/validation errorとし、旧backupで上書きしません。load後にfuture documentが置かれた場合も、共通saveでUnsupportedSchemaErrorを伝播して上書きを防ぎます。JobStateSave isolationは維持します。

## cleanup

作業前のprojectは36,050,748,517 bytes、360,132 filesでした。workは32,752,211,838 bytes。全fileをA（保護）、B（現行正式成果物）、C（再生成可能）、D（判断不能／保持）へ分類しました。Git tracked、.git、.venv、source/tests/docs/config、production JSON、Chrome profile、RAW・完成MP4・HLS・user ZIP・台本・Ending・移行資料を保護します。

第1段では11,197 files／2,024 directories、16,430,071,802 bytesを削除しました。旧配布ZIPは中身を確認したVer2.5配布物1件だけです。他の削除ZIPは24個の再生成可能なPython base_library.zipです。拡張子だけでuser ZIPを選択していません。旧acceptanceのプログラム／依存DLL、生成build intermediate、cache、旧GUI screenshotを主に整理し、旧work RAW・各profile・runtime JSON・成果物は保持しました。

削除manifestはwork/v27/deletion-manifest.json、実績はwork/v27/deletion-result.jsonです。Windowsの通常filesystem APIを使い、絶対pathのworkspace包含・Git tracked非該当・reparse point不在・size/mtime一致を再確認しました。保護対象と不明なfileを自動削除せず、空になった生成directoryだけを除去しています。削除失敗0。cleanup由来のtracked削除は0で、作業中のGit差分は今回の許可済みsource/tests修正のみです。最終commit後にGit cleanを確認します。

## 検証とrelease順

基準1021 tests PASSから、schema 16件とcleanup planner 27件を追加し、全1064 testsがPASSしました。cleanup後にproduction settings/presets/Jobの読取、app import、隔離GUI起動を確認しました。修正入りVer2.6候補EXEでschema restart・future schema保護・Job保存隔離・Quotaを確認した後にVer2.7へ昇格しました。

並行検証で既存のモーダル試験にタイミング競合が出たため、2件のfixtureの人間役を「モーダル待機の検出後に閉じる」同期へ変更しました。製品のStudio／modal操作処理は変更していません。誤click 0・重複Generate 0・外部tab保持のassertionを維持しています。

正式Ver2.7の全1064 tests、EXE Acceptance 12系統、portable、GUI再起動、ZIP CRC・SHAを確認した後、記録済みSHAとarchive内fileのCRCに一致する未使用のVer2.6正式配布物を除去しました。Ver1.2.6の旧ZIPも中身と履歴上のfactory configを照合して除去しました。最終distはVer2.7のEXE folder・ZIP・SHAのみです。再build用のreviewed dependenciesとlicenseは保持しています。

追加の検証コピー整理まで含め、削除は延べ14,715 files／2,419 directories、22,623,687,526 bytes、失敗0です。再生成された検証物の削除も含む累計なので、実際のproject純減は最終auditのbefore→afterで別に報告します。production関連40 filesは元のhashと全件一致し、tracked file・RAW・完成MP4・HLS・user ZIP・実profileの削除は0です。整理後の隔離GUI起動もPASSしました。削除manifestと実績はwork/v27のdeletion-{post,archive,future}-manifest.json／対応result.jsonにも保存しています。
