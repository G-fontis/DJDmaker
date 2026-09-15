# Ver1.2.8 未処理jobとCloud優先復旧

指示: DJD-CHAPPY-V128-UNPROCESSED-JOBS-CLOUD-PRIORITY-RECOVERY-FULL-001
開始HEAD: aa97fd3c6ff53b74ea13e41cee5391d4764a64c6 / main、Git clean。
添付の855640e/未commit/26 FAILEDは旧情報。前回の26件解消・成果物保持修正を維持する。

## 実データで確認した根因

本番 `DJDmaker_Ver1.2.8/system/jobs` は393件、すべてWAITING。
Notebook identity/generation_started_at/RAW/ZIP/duplicateは全て0。source_status UNKNOWN393。
旧 `_needs_dispatch` でもgeneration candidateは393であり、「候補0」や表示文字列依存は再現していない。
`work/v128-cloud-priority-audit-001/summary.json`に記録。

本番cloud-limit.jsonはactive=true、message=QUOTA_EXHAUSTED、cloud_blocked_until=null、cloud_resume_at=null。
旧CloudLimitGate.dueはresumeが非nullの場合だけtrueになる。従って現在UI再確認に一度も到達せず、
capability=falseが393件を一括抑止。Local優先/10分Waitは、その誤ったcapabilityに基づく結果だった。
Notebook待機/End待機/HLS待機という表示はbackend判定の根拠ではない。

## 修正

- 期限なし/不正/期限切れは再確認対象。現在UIでChat enabled＋実Limitなしを確認した時だけ解除。
- 直近の現在Quota返信は即解除せず、120秒のbounded backoff。期限がある実Limitは+5分まで維持。
- 確認失敗や利用不可ではCloudを開けない。次の再確認時刻に合わせ待機し、無期限/10分固定を避ける。
- 完全未処理の前工程レコードをWAITINGへ再分類。既存Notebook/送信/生成/local checkpointやduplicate/OUTPUT_BLOCKED/terminalは消さない。
- Local/Download/Wait前の生成可否再計算を維持・補強。候補数とcapabilityを区別してログに出す。
- 両GUIに「再確認必要」と候補件数を表示。Version1.2.8のまま。

## live確認

`work/v128-cloud-live-check-001/result.json`: PASS。
既存本番profileと既存方式で現在Notebookを確認し、Chat利用可能/実Limitなしを確認。
コピー側cloud gateはLIMIT_UNKNOWN_NEEDS_RECHECK→AVAILABLE。生成/create/upload/send0。
本番system JSON1,379件のhash前後変更0。原本cloud-limitも変更しない。

本番393件のPreset記録は全て空。ユーザーが「ペーパークラフト_日本語」を指定したため、
登録名「ペーパクラフト_日本語」を試験コピーへ適用。
旧installの274 jobと実出力名を照合し、PC000を1件だけ使用した。

`work/v128-cloud-generation-live-001/verified-result.json`: 実生成PASS。
Notebookを1件作成、Source READY後にPresetを1回送信。URL/identityはローカル証跡JSONへ保存。
本文readback/今回user message/今回返信を確認し、StudioのGENERATINGを検出した。
後続の読み取り専用確認でREADY動画1件（UI表示8:03）を確認。追加send/create/upload/Download/deleteは全て0。
証跡: `work/v128-cloud-generation-live-001/final-artifact.json`。
Local/Download/deleteは0。本番JSON1,379件の変更0。原本TXTも保持。
最初の試験helperは通常アプリが渡すPersistentPollSchedulerを省略していたため、
generation_started_atの追加assertだけが失敗した。失敗結果は保存し、再生成せず実観測を照合。
既存restart補完APIで試験コピーの時刻のみ補完し、既存next_poll_atを保持した。
通常アプリのScheduler実装変更は不要。

試験Notebookはremoteに保持され、本番job原本へはidentityを反映していない。
このPC000を通常運用で再投入する前には、試験コピーのNotebook identityを照合する必要がある。

両GUIの再確認必要/Generation候補393/Local候補0/Download候補0を実描画確認。
証跡: `work/v128-cloud-gui-001/`。旧26 FAILEDは前回commitで解消済み。
全件test、build/EXE/package、commit/pushは完了するまでPASSにしない。

## 最終source Gate

- `v128-cloud-full-003`: 783 passed / FAILED 0 / ERROR 0 / skipped 0（54 test files）。
- 既存746件を維持し37件を追加。compileall、git diff --check PASS。
- 全件再実行002では1件の実回帰を検出した。直前のNO_OP_JOB_TRANSITIONを未処理へ再分類していたため、
  明示的な処理エラーは既存のbounded recovery/identity照合へ残すよう修正。期待値は変更していない。
  NO_OP・SUBMISSION_STATE_UNCERTAIN・NOTEBOOK_STAGE_FAILED・LOCAL_FILE_MISSINGの保持testを追加。
- 比較元phase2はf6f5871925f44718f51a106f7f55fa44e8f882b1のまま。source/mainのみ修正。
- 正式build scriptをそのまま実行し、PyInstaller onedir build / release-tree preflight PASS。
  FileVersion1.2.8.0、ProductVersion1.2.8。

## 配布物

- Portable: `dist/DJDmaker_Ver1.2.8/`
- EXE: 4,064,839 bytes / SHA-256 `BCA440BD3391B9362AC028430EAC9BD8F11EEF5AEC9D832B18232C2A2AC4C909`
- ZIP: `dist/DJDmaker_Ver1.2.8.zip` / 277,859,045 bytes
- ZIP SHA-256: `10DBB65F2E701DD4CFCACC013844AB246A7E7E89B6213F4973F39CFB8D8B16AE`
- 408 files、ZIP CRC PASS、日本語・空白pathへ展開後の全file SHA一致。
- 配布物のruntime media/profile/credentialsは0。本番Dropbox installのEXEは置換していない。
- 展開後GUI選択保持、settings保存/再起動復元、browser smoke、Fake E2E COMPLETED PASS。
  Fake E2E初回は試験用Preset準備が不足して停止した。例外画面の内容を保存し、既存preset smokeで準備して再実行PASS。

- EXEのlimit/回収/保存失敗隔離/Shutdown/逐次処理/lifecycle/GUI選択復元を全てPASS。
  limit再探索間隔808.02秒、lifecycle再探索間隔600.07秒（ともに早期再探索なし）。
- 変更5moduleのfrozen codeはテスト済みsourceと一致。最終22 report全PASS、配布hash不変、
  本番JSON1,379件変更0、配布runtime混入0、phase2 ref不変をcommit前に再確認。
  証跡: `work/v128-cloud-release-guard.json`。

SOURCE_LIVE_EXE_PACKAGE_GATE: PASS
Git commit/push後の最終SHAと一致確認はローカル最終報告書へ記録する。
