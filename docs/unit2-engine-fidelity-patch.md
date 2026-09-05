# Unit 2 エンジン忠実性補正

指示ID: `DJD-CHAPPY-V01-UNIT2-ENGINE-FIDELITY-PATCH-001`

対象指示ID: `DJD-CHAPPY-V01-UNIT2-GUI-SCHEDULER-LIVE-DOM-001`

この文書はUnit 2の追加・補正仕様であり、既存のUnit 2指示と矛盾する場合はこちらを優先する。元3エンジンで実現済みの機能を、調査不足やselector未確認を理由に削除、簡略化、無効化して最終仕様としてはならない。

## 実装判断の優先順位

1. 今回の確定仕様
2. 元3repoの最新実装
3. 元3repoのテスト
4. 元3repoの設計資料
5. DJDmaker向けadapter化

不明点が残る場合は仕様を推測して実装せず、元repo、関連service、adapter、selector、test、helper、docs、git historyを調査した証跡とともにチャッピーへエスカレーションする。

## 元3エンジン忠実性

- GNBCreator: Notebook作成、TXT投入、動画生成、監視、download、生成済み動画artifactのみのWeb UI削除を既存実装優先でadapter化する。
- ドウガッチンガー: 最終音声の逆探索、検出位置への`+0.5秒`、Ending結合、FFmpeg/ffprobe検証を既存実装優先で維持する。
- HLS Converter: 元HLS command、6秒segment、playlist検証、`ZIP_STORED`、ZIP integrity検証を既存実装優先で維持する。

独自実装へ置き換える場合も、元実装の処理順、安全策、テストと同等以上であることを示す。

## 動画artifact削除の必須安全ゲート

次の順序を変更しない。

1. Download
2. 一時ファイル終了確認
3. MP4存在確認
4. `size > 0`
5. size stable確認
6. ffprobe PASS
7. video stream確認
8. duration確認
9. `raw_files`保存
10. RAW再検証
11. 全条件PASS
12. Web UIから対象動画artifactのみ削除

Notebook本体削除は禁止する。source TXT削除は本Unitの対象外とする。削除操作をクリックしただけでは成功扱いにせず、対象artifactのDOM/listからの消失、対象メニューの消失、または成功toastなど、対象が削除されたことを示す状態変化を確認する。

削除失敗時はRAWとNotebookを保持し、ジョブを再試行可能状態にする。他ジョブの処理は継続する。

## Live DOM診断方針

目的はゼロからselectorを設計することではなく、GNB_Creatorの既存action sequenceが現行NotebookLMでも有効か確認することである。

1. GNB_Creatorの動画artifact削除実装を特定する。
2. selectorとaction sequenceを抽出する。
3. DJDmaker adapterへ移植する。
4. 現行NotebookLMで最小限の確認を行う。
5. UI変更があれば、診断証跡に基づいてfallbackを追加する。
6. 成功、誤対象拒否、削除未確認、retryをテストする。

診断時にHTML、スクリーンショット、accessibility情報を保存する場合は、元GNBのprivacy redaction方針を維持し、認証情報やユーザーデータをGit管理しない。

## 2026-09-06時点の元実装調査証跡

一次資料は `C:\xampp\htdocs\PHP\AutoGeminiNoteBookCreator` の `main` HEAD `28bd51dfe2894018bfc9d65a02f219a933199127` とした。`main`、全commit履歴、tracked filesを対象に、`delete`、`remove`、`artifact`、`video`、`download`、`menu`、`more`、`three dot`、`confirmation`、`trash`、`overview`、および日本語の削除関連語を検索した。

重点確認ファイル:

- `app/automation/notebook_deletion.py`
- `app/automation/remote_recovery.py`
- `app/services/batch_processor.py`
- `app/services/phase3_finalize.py`
- `app/automation/selectors.py`
- `app/tools/notebooklm_diagnostic.py`
- `app/services/dom_diagnostic.py`
- `tests/test_notebook_deletion.py`
- `tests/test_remote_recovery.py`
- `tests/test_download_manager.py`
- `tests/test_dom_diagnostic.py`
- `README.md`

確認できた動画artifact側の既存動作は、完成動画カードに対して `artifact-library-item` を扱い、カードの「その他」ボタンを開いてメニュー状態を診断・採取する処理、および同じartifact文脈からdownloadする処理である。`notebooklm_diagnostic.py --probe-video-menu` はこのメニューを開き、`diagnostics/20260905_101556/STATE_10_VIDEO_MENU` に診断情報を保存している。同診断の `accessibility.txt` 465行目に `role=menuitem name=削除`、`elements.json` に `artifact-more-button` と削除menuitem、`page.html` に完成動画カード内の「再生」「その他」と開いたメニューの「ダウンロード」「削除」が記録されている。この保存済みlive DOMを動画artifact専用menu sequenceの一次証跡とする。

一方、確認できた削除実装 `NotebookDeletionService` は動画artifact削除ではない。Notebook一覧 `https://notebook.google.com/` へ移動し、対象Notebookリンクを含む行の「プロジェクトの操作メニュー」を開き、「削除」、dialog「このノートブックを削除しますか？」、確認「削除」の順に操作し、対象Notebookリンクのdetachを待つNotebook全体削除である。`remote_recovery.py`、`batch_processor.py`、`phase3_finalize.py` からもこのserviceが呼ばれている。

全git履歴の同種ファイルとselector参照も確認した。productionの自動削除serviceとして確認できたものはNotebook全体削除だけだが、保存済みlive DOMにより、動画artifactカード内の「その他」から動画専用メニューの「削除」へ進むaction sequenceは確認できた。artifact削除後のconfirmation dialog、成功toast、削除完了状態の保存済み診断は特定できなかった。

## 現時点の移植可否と安全判断

DJDmakerの `NotebookDomAdapter` へ、保存済みlive DOMを根拠とするartifact-only action sequenceを移植した。対象titleと再生buttonで完成動画を一意にscopeし、「その他」／`More` fallback、開いた単一menu内の「削除」／`Delete` fallbackの順に操作する。12項目の安全ゲートを操作前に必須とし、通常pipelineにはNotebook全体削除APIを公開しない。

confirmation dialogは元診断で未確認のため任意として扱う。表示されたdialog本文に `Notebook` または「ノートブック」が含まれる場合は即時中止し、確認buttonを押さない。成功は対象カードの非表示／消失、または明示設定した新規成功toastだけで判定し、不明な結果はretryable fail-closedとする。

Unit 1の無条件無効状態はUnit 2の最終仕様としない。保存済みlive DOMと互換fixtureに基づくartifact-only selectorを既定で有効にし、明示的に無効化する注入点も保持する。現在のGemini Notebookへの再適合確認だけは、利用可能なブラウザセッションが存在しないため未実施である。

## 現行live確認のpending事項

- 実現したい機能: RAW安全ゲート通過後、Notebookを保持したまま生成済み動画artifactだけをWeb UIから削除し、DOM状態で成功を確認する。
- 調べた3repo: `AutoGeminiNoteBookCreator`、`GeminiNotebookVideoMerge`、`FukuzemiApp`。
- 発見した関連コードと診断: 動画artifactカードの「その他」メニュー診断、artifact文脈のdownload、privacy-redacted DOM診断、保存済み動画menuの「削除」、Notebook全体削除service。
- 分からない点: 現行UIでconfirmation dialogが出るか、成功toastがあるか、2026-09-05診断後にselectorが変更されたか。
- 実装済み方針: 保存済み診断の `artifact-library-item`、再生button、「その他」、単一menu、「削除」を踏襲し、対象カード消失を成功条件とする。dialogは動画用と安全確認できた場合だけ操作する。
- 勝手に決めない項目: 未確認のtoast文言やdialog名をproduction既定値にしない。Notebookを示すdialogは常に拒否する。
- live未確認理由: この実行環境で利用可能なブラウザセッションが0件であり、認証回避や重要Notebookでの破壊確認は行わない。
- 必要な回答: 現行live適合確認まで行う場合は、ブラウザセッションを接続し、重要データを含まない診断用Notebookを1件用意する。

live確認待ちでも、既存機能を「不要」として削除したり、Notebook全体削除へ置換したりしない。元実装・保存済みlive診断の調査、DJDmaker移植、fail-closedテストを完了しているため、他のUnit 2条件が通過すれば `PASS_WITH_LIVE_DELETE_PENDING` の候補とする。

## Unit 2完了報告の必須追加項目

33. GNB_Creator動画artifact削除元実装の所在
34. 元実装の処理順
35. 元selector / action sequence
36. DJDmakerへの移植内容
37. live DOMでの適合確認結果
38. 元実装から落とした機能が0である確認
39. 元3repo検索後も不明だった機能一覧
40. チャッピーへのエスカレーション要否

動画artifact削除は、元実装の調査と移植、または現行UIで動作不能である十分な立証なしに `DELETE_ARTIFACT_UNVERIFIED` のまま通常PASSとしてはならない。`PASS_WITH_LIVE_DELETE_PENDING` は、元実装を調査済みで、かつ現行UI変更など外部要因を証跡付きで確認した場合に限る。
