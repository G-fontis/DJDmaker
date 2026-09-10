# Ver1.2.8 Notebook再照合・動画保持（作業中）

指示ID: DJD-CHAPPY-V128-NOTEBOOK-FAILED-RECONCILE-PRESERVE-ARTIFACT-FULL-001

追加指示: COMPLETEDの実outputを照合し、不要jobをアプリのロジックで整理する。

HEAD before: `855640e1896f4bfd0a78d921b1c018f9c62641fb`。main。全Gate前のcommit/push禁止を維持。

## JOBST03の事実

- Ver1.2.8の`0b5b53c76ea2499aa577beeae30ac72e`はFAILED / NOTEBOOK_STAGE_FAILED、TXT欠落、Notebook ID/URLなし。
- 同一台本の`0c0c84529d944b74a8d869f513b121cc`はVer1.2.7の実記録でCOMPLETED。Notebookは`143d0121-cd16-4500-84fe-d08df248a17c`。
- Ver1.2.8では後者は`deleted_jobs.json`に記録され、通常job JSONは削除済み。migration backupは完了時のsnapshotではない。
- RAWは42,537,363 bytes、ffprobe PASS、489.314104秒、H.264/AAC、1280×720。
- ZIPは19,714,465 bytes、CRC PASS、HLS playlist終端あり、全63segment存在。
- 退避TXTは3,917 bytes、SHA-256は完了jobのsource_sha256と一致。元inputには存在しない。
- 読み取り監査: `work/v128-jobst03-record-cleanup/audit.json`。手動削除は実行していない。

つまりremote生成失敗ではなく、完了job削除後に残った未生成重複jobが、移動済みのTXTを読もうとして失敗した事例。別jobのNotebook IDがないことを「作成したNotebook IDを失った」と断定しない。

## 修正内容

- `submit()`の既存remote確認より前にTXTを読む経路を修正。新規作成・実upload時に限りTXTとdigestを検証。
- LOCAL_SOURCE_FILE_MISSINGとremote状態を分離。FAILEDも期限到達時にPriority 3で確認。READYはDownload、GENERATINGはWAITING_VIDEO、真のFAILEDは既存のbounded Preset retry、UNKNOWNは再診断。
- 片側のみ残ったID/URLは検証して復元。両方欠落時は既知source digestとpathが一致し、候補Notebookが一意の場合のみ復元。曖昧なtitleだけで紐付けない。
- Download後とローカル再開時の自動artifact削除を撤去。旧DELETE_PENDINGも保持へ移行。独立した明示削除APIだけ残す。
- RAWの12項目gateは維持し、満たさない場合はDownload検証失敗としてローカル処理へ進ませない。
- Job詳細にremote source / artifact、local error、recovered checkpointを分離表示。

## 自動的な不要job整理

- 起動・台本再読込・schedulerの所有権再照合経路に統合。
- COMPLETEDと同じsource identity、preset整合、現在outputのZIP CRC/HLS playlist/全segmentを照合。
- 独自Notebook、生成開始、RAW、編集動画、HLS/ZIP checkpoint、保存保留があるjobは削除しない。変更済みsourceや不正/欠落outputも保持。
- 不要job JSONは`system/removed-job-records/<job-id>.json`に退避後、通常一覧・queueから除去。tombstoneで再読込時の復活を防止。
- COMPLETED削除時も今後は完了snapshotを保存して再照合に利用。
- 旧tombstoneも所有権の証拠が必要。pathだけ、source hashなし、ZIP内容との結び付きなしの場合は自動整理しない。現行Gateはsource SHAと保存ZIP SHA、または検証済みHLS全内容との一致を要求する。新しい入力や曖昧な根拠は保留し、別installを探索して結合しない。CRCだけで所有を確定しない。
- Notebook・source・動画・RAW/HLS/ZIPは削除しない。

## 検証の区別

以下は先行作業時点の履歴。26 FAILED解消とその後のbuild/Acceptanceは `v128-regression-resolution.md` を参照する。過去の失敗証跡は削除しない。

- 追加確定: エラー解除は実成果物を基準とする。正常な所有ZIPがあれば再生成せず完了へ復旧。同名だが所有不明/不正な既存ZIPや動画/HLSは無断再生成・上書きをしない。COMPLETEDでも現在の所有ZIPが欠落した場合はRAW/remote確認を経て復旧し、本当に未生成の場合に生成する。
- 最新関連テスト54 PASS。保存隔離/Quota/Pause/回収等118 PASS（重複実行を含むため合算しない）。
- 全728テスト実行結果: 702 PASS / 26 FAILED / errors 0 / skipped 0。`work/v128-output-recovery-full-002-tests.json`および両suite XML/log参照。既存のremote再照合・Quota復旧・逐次処理・旧artifact削除期待等の失敗を未解決として残す。全体PASSではない。先行runは修正追加のため中断しており成功扱いしない。
- 最新の成果物重複防止テスト17 PASS。関連90 PASS（追加前、重複を含む）。人間Startのresume経路とschedulerの両方で既存ZIP時の生成依頼0・上書き0を確認。保存失敗復旧でもOUTPUT_BLOCKEDを維持する。
- compileall PASS。git diff --check PASS。
- live 001はrequired selectors Gateで停止。live 002はGoogle認証未確認で停止。
- どちらもPreset送信0、upload0、artifact削除0。本番system JSON 1,346件について前後変更0。
- live 004 PASS: コピー側に再現したFAILED+TXT欠落から、実JOBST03 READY→Download→RAW 12 gate→Ending skip→HLS/ZIP→COMPLETED。Download後・COMPLETED後ともremote artifact READY。本番system JSON 1,346件の変更0。Preset再送0、upload0、artifact削除0。証跡: `work/v128-retention-live-004/result.json`。
- 自然modalは本番同一guardでBLOCKING_MODAL:ANNOUNCEMENT→MODAL_DISMISSED:dialog_hidden:scrim_goneを確認し処理継続。live 003の確認用helperにguard指定がなかった点を補正したもので、本番guardの変更ではない。
- build / EXE / package / commit / push未実施。既存配布EXEには今回の修正は未反映。live 004は新規outputの回収試験であり、その後追加した既存output guardのlive検証と混同しない。

## 残工程

全テスト結果確認、必要な回帰修正、認証を回復した同一方式でのlive確認、正式portable/EXE/package監査。全Gate前にrelease PASSとしない。

DJDMAKER_V128_NOTEBOOK_FAILED_RECONCILE_ARTIFACT_RETENTION_RESULT: BLOCKED
