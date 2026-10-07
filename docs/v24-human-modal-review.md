# UNKNOWN modalの人間確認と自動復帰

指示ID: `DJD-CHAPPY-V24-UNKNOWN-MODAL-HUMAN-REVIEW-FULL-002`

分類できないNotebookのdialog/overlayは、自動dismissせず
`WAITING_FOR_HUMAN_MODAL_DISMISSAL`として待機する。GUIには大きく
「Notebookに確認が必要な画面が表示されています。ChromeのNotebook画面を確認し、
表示されているモーダルを閉じてください。閉じると処理は自動的に再開します。」を表示する。

約2秒ごとにdialogとblocking scrimを監視する。人間操作待ちに短時間timeoutを設けず、
返信・DOM観察の期限から待機時間を除外する。close/X/Enter/Escapeを自動操作しない。
Stopとアプリ終了は通常の協調キャンセルで扱い、待機表示を解除する。

dialog非表示、scrim消失、同じNotebookのURL/identity、操作対象のtrial確認を満たして復帰する。
既存Notebookタブがあれば再activateする。外部タブは閉じない。
同じNotebookが見つからない場合は画面を開くよう案内し、勝手に別Notebookへ移動しない。
Download転送中の復帰・close/navigationは既存Download Guardで保護する。

待機状態はruntime表示のみであり、job本文・checkpoint・FAILED/TERMINALを変更しない。
復帰イベントでrepository、capability、candidate discoveryを更新し、既存のSource・返信・artifact
readbackを続ける。生成中/完成済みartifactを確認できた場合は、返信文が異なってもPresetを再送しない。
人間が閉じたmodalをknown分類へ自動学習しない。

既知ANNOUNCEMENT/ONBOARDING/SAFE_INFOの従来dismissを保持する。
AUTH、Usage Limit、明確なERROR、同意・購入・削除確認等はUNKNOWN待機に混ぜない。

## 検証記録

広告の実modalは再表示されなかった。ローカルの実DOM fixture、実Chrome、PHASE1/PHASE2 GUI、
実Notebook上の検証用dialogで人間待機と復帰を確認した。
実Notebookの検証用dialogを手動closeした後、Notebook作成・Source upload・Preset送信は各1回。
同じNotebookで`GENERATING`を読み直し、追加送信を禁止したcheckpoint検証がPASSした。
Notebook・Source・artifact・本番RAW/MP4/HLS/ZIPの削除は行っていない。

最初のlive検証では既存返信classifierが返信文から生成開始を判定できず、artifact検出で再送を防いだ。
その経路をartifact readbackによる復帰へ修正し、生成中/完成済みの新規回帰テストを追加した。
この初回のエラーを広告modalの再現結果とは扱わない。

実Chromeの所有範囲StopテストはGoogle初期遷移に依存しないローカル起動画面で実施する。
実Chrome・OS所有境界・停止10秒以内という検証条件は維持する。
開発依存にpytest-qtを記録し、既存qtbotテストを実行可能にする。

Frozen EXEのopt-in検証は`DJD_PACKAGING_SMOKE=1`と
`--packaging-human-modal-smoke <新規検証ディレクトリ> <report.json>`で実施する。
fixture内で人間操作相当のDOM消失を発生させ、自動close 0、外部タブclose 0、GUI応答、
自動復帰、checkpoint保持、Stop、アプリ終了を確認する。実広告modalのlive再現とは区別する。
検証用ファイルとreportは正式packageへ含めない。

既存EXE acceptance helperも現行Ver2.3のcheckpoint・品質仕様に合わせた。
回復・連続処理は、小容量fixtureで発生する最大6 attempts以内のDownload再試行と
`SKIPPED_BY_SETTING`/`local.skip`を検証する。Preset送信はjobごとに1回、
完成後の再実行による新規操作は0回を保持する。
Limit fixtureは有効なNotebook UUID、検証済みRAW、回収可能なfake handoffを使う。
製品の品質threshold・Limit判定・remote保持条件を緩める変更は行っていない。

実Chromeのloopback検証ではnative popupを1回発生させ、実MP4の保存完了・
byte一致・ffprobe確認の後にDeferred Queueをreleaseする。転送中のcloseは保留し、
Stopを次jobより先に処理する。これは実Google artifactのDownloadとは区別したfixture検証である。

## 最終Release Gate（2026-10-07）

- 全952 tests PASS（既存群914＋人間確認関連38）、FAILED 0、ERROR 0。
- 最終EXE: human modal、native popup Download完了、shutdown、recovery、Limit中回収、
  lifecycle、sequential、save isolation、HLS optionalの9シナリオすべてPASS。
- Portable GUI/設定再起動/Preset/Chrome/Fake E2E PASS。PHASE2→PHASE1の別process復元もPASS。
- 本番system/config 38ファイルhash一致。job JSON/settings/presets変更0。
- Package audit PASS。409 files。profile/Cookie/credential/ユーザーruntime/job state/
  screenshot/debug log/RAW/MP4/HLS/ZIP data混入0。公式FFmpeg/ffprobe依存は同梱する。
- Python/Product 2.4.0、FileVersion 2.4.0.0、GUI Ver2.4。
- `DJDmaker_Ver2.4.zip`: CRC検証PASS、278,043,744 bytes。

EXE SHA-256: `7cf9da3c0c1fda4bd6e02fb18c5ac9eb397fd6c8b8272632e199022986f4b5c2`

ZIP SHA-256: `9827d7b259f2603e1235d0a90544d3cefc89d214759e200899006887a9ee4ec0`

実広告modalはlive未再現。実Notebook上の検証dialogで手動close→自動処理復帰を確認し、
Notebook作成/Source upload/Preset送信/生成開始は各1回。重複upload/送信/生成要求は0。
同じNotebookの生成中artifactをcheckpointから再確認した。検証用Notebookは削除していない。
