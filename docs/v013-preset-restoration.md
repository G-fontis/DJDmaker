# v0.1.3 GNB動画生成プリセット復元

## 一次資料

基準はAutoGeminiNoteBookCreator HEAD `28bd51dfe2894018bfc9d65a02f219a933199127`。主な所在は`app/services/preset_service.py`、`app/ui/preset_dialog.py`、`app/ui/main_window.py`、`app/automation/notebooklm_page.py`、`app/automation/notebook_processor.py`、`app/config/selectors.py`、`tests/test_data_services.py`、`tests/test_ui.py`である。

元実装はSQLite `presets` tableで名前、本文、作成・更新日時を保存し、名前はtrim後の空欄、本文は空白のみ、名前のcase-insensitive重複を拒否する。一覧は名前順。新規、編集、複製、削除確認を持つ。default presetのseedは存在しない。削除後は再読込され、残りがあれば名前順先頭、なければ未選択になる。

元GUIは選択preset本文を「今回使用する文章」へ読み込み、batch開始時にその編集内容をsnapshotする。動画生成では`VIDEO_OVERVIEW_CREATE`で「動画解説をカスタマイズ」を開き、`VIDEO_CUSTOM_TOPIC`の「この動画で重視するポイントは何ですか？」へ本文を`fill`した後、即時生成ボタンを押す。

最新GNBは選択ID自体をsettingsへ保存しておらず、再起動時は名前順先頭がQComboBoxで暗黙選択される。今回の確定要件に従い、DJDmakerでは選択IDを明示保存して同じpresetを復元する。

## DJDmakerへの移植

- `system/presets.json`へversioned envelope、preset一覧、`selected_preset_id`を保存する。
- 既存JSON層のprocess内RLock、temporary file、flush/fsync、atomic `os.replace`、backup/recovery、bounded transient retryを再利用する。preset保存用lock fileは作らない。
- 設定画面へ「動画生成プリセット」、選択、新規登録、編集、複製、削除を追加する。
- default presetは新設しない。未登録・未選択時はNotebook作成前に「動画生成プリセットを登録・選択してください。」で停止する。
- Start時の選択presetをpipelineへ渡し、各jobのpreset ID、名前、本文をJSONへsnapshotする。
- TXT uploadとsource ready確認後、動画解説カスタムトピックへsnapshot本文を入力し、その後に生成を開始する。
- preset変更は次回Startから反映し、実行中jobのsnapshotは変更しない。

## 他のGNB GUI・設定機能の欠落監査

今回の確定範囲外なので追加実装していない候補は次のとおり。

- 「今回使用する文章」で保存presetを変更せずbatch単位に上書きする機能。
- 「今回生成する本数」によるbatch上限指定。
- `generation_timeout_minutes`と停止語一覧のユーザー編集。
- 独立した「動画を確認・回収」「再開」ボタン。DJDmakerにはscheduler、自動restart recovery、ジョブ詳細retryがあるがUI構成は同一ではない。
- GNBの即時生成／クレジット不足時予約fallbackに関する表示項目。DJDmaker schedulerの目的と状態表示は異なる。

Googleログインの通常Chrome、同一専用profile、browser handoff、入力/output path、download安全gate、artifact限定削除はDJDmakerに存在する。旧GNBユーザー固有SQLiteからの自動importは行わない。必要なら別仕様として判断する。

## Live確認

2026-09-06、既存のDJDmaker専用profileでPre-flight 7/7 PASS後、新規Notebookへ`DJD_V013_PRESET_LIVE_001.txt`を投入した。生成開始前にカスタムトピック欄へ識別可能な文章Aを入力して完全readbackし、同じ欄を文章Bへ切り替えて完全readbackした。artifact 0件を確認してからBだけで生成をクリックし、生成中artifactが1件だけ作成されたことを確認した。2件目は生成せず、Notebookとsourceは保持した。
