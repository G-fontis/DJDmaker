# Ver2.0 HLS＆ZIP任意化

指示ID: DJD-CHAPPY-V20-HLS-ZIP-OPTIONAL-FULL-001

## 設定と実行単位

- 共通AppSettingsの`hls_zip_enabled`は既定true。旧settings/job JSONのキー省略もtrue。
- Phase1/Phase2は同じ設定ダイアログ、保存先、パイプラインを使用。
- Start時の値をパイプラインに固定し、未完了jobに保存。実行・Pause中の設定チェックボックスは変更不可。
- 次の新規Startでは未完了jobにその時点の設定を適用。COMPLETEDのモードは変更せず、再生成しない。
- 保存保留journalから復旧したjobも変換直前に現在runの固定値を照合する。

## ON / OFF

ONは従来のRAW→任意Ending→HLS→ZIP→COMPLETEDを維持。

OFFはRAWの既存12項目安全gateと動画検証を通した後、任意Endingを実行し、
`<出力先>/completed_mp4/<台本名>.mp4`へ完成MP4を保存する。
RAWと出力先が同一でもRAWを置換しない。HLSとZIPは`SKIPPED_BY_SETTING`。
既存HLS/ZIP・Notebook・source・remote artifactは削除しない。

完成MP4は同一directoryの一時MP4へコピーし、flush/fsync、ハッシュ照合、動画検証後、
pathとSHAをjobに先行保存。存在しない正式pathへの排他的hard-linkで原子的に公開する。
既存ファイルの上書きは行わない。公開後の中断は保存済みSHA・source識別・RAW gateで照合する。
一時コピーのみfinallyで除去し、RAW・既存正式成果物は保持する。

OFF完成はZIP不要。SchedulerはCOMPLETEDを終了と扱い、HLS待機へ入れない。
完成後のTXT移動失敗はCOMPLETEDを戻さない。

## 明示的なHLS再処理

OFF完成後にHLSが必要なら、停止状態で設定をONにし、ジョブ詳細の「HLSから再実行」を指定後、開始する。
所有権・動画検証済みの完成MP4を同じjobで再利用し、Notebook生成・Preset再送をしない。
設定をONへ変更しただけでは完成jobを再処理しない。

## Source / live検証記録

- 追加38 tests（設定、既存JSON互換、OFF各再開state、複数job、保存失敗、原子的公開、完成MP4重複照合・破損・情報欠落、共有GUI、辞書化互換）。
- 実FFmpegによるON/OFF×Ending有無の4通りでCOMPLETEDとTXT MOVEDを確認。
- KAIGO091（Notebook ID `45a1fc35-7eea-433c-ade6-73c773e0ebda`）をユーザー指定で使用。
  ON: 実Download→RAW gate→HLS→ZIP→COMPLETED→TXT MOVED。
  OFF: 実Download→RAW gate→完成MP4→COMPLETED→TXT MOVED。HLS call 0 / ZIP event 0。
- ON/OFFとも回収後のremote artifactはREADY。Notebook作成・Preset送信・upload・deleteは0。
- 本番system JSON 2,992件と対象実RAW/ZIP/TXTの計2,995ファイルをhash照合し、変更0。
- live証跡: `work/v20-live-002/result.json`。実メディアと試験JSONはcopy側のみ。
- 以前のPC000試験URLはホームへ戻り「ノートブックが見つかりません」と表示されたため、
  未生成とは扱わず、指定されたKAIGO091へ試験対象を変更した。新たなremote生成は行っていない。
- 初回全体回帰は816件中6件失敗。3件はZIP所有権検証の旧オブジェクト互換で、
  mode未指定をONとする修正後に対象10件PASS。残りは処理時間の制限超過。
  175-job計測では76,775回の辞書化が主負荷だったため、JSON形状とmutableコピー分離を保ちつつ
  immutable scalarの再帰走査を省略。175-jobを含む対象43件PASS。
- 全体回帰819件PASS、追加テスト最終38件PASS。空queueの即時正常終了とPauseが競合する旧testは、
  未完了の将来待機jobを置いてPause対象を維持するfixtureへ修正。Pause実装・assertionは弱めていない。
- current sourceでlive完成copyを再確認し、ON/OFFともNotebook操作・Download・変換・job JSON変更0。
- source/live Gate後にPython/Product 2.0.0、FileVersion 2.0.0.0、GUI Ver2.0へ変更。
- 既存PyInstaller onedir buildは正常終了。ExecutionPolicy回避なし。
  bundled変更20モジュールを現在sourceのcompiled codeと照合して一致。
- 最終portable・展開後・cleanup Gateの結果は以下のとおり。

## 正式候補の固定情報

- 最終source回帰: **821 passed / 0 failed / 0 error / 0 skipped**（398.98秒）。既存783＋追加38。
- `compileall src packaging` / `git diff --check`: PASS。
- Portable: `dist/DJDmaker_Ver2.0/`、既存PyInstaller onedir方式。
- EXE: 4,081,389 bytes、FileVersion 2.0.0.0 / ProductVersion 2.0.0。
- EXE SHA-256: `00AFC1050FE1E4152547E6961A8EAA312A143B4E491CECEDEC027ABE9B9B5765`
- ZIP: `dist/DJDmaker_Ver2.0.zip`、277,872,627 bytes。
- ZIP SHA-256: `EC242AEDF98AE492769F1C68B7572682C25F32B7D538FE93284277979613061B`
- Package全408ファイル: runtime/media/profile/credential混入0、ZIP CRC PASS。
  日本語＋空白pathへ新規展開し、全ファイルSHA一致。
- checksum: `dist/DJDmaker_Ver2.0_SHA256SUMS.txt`。

## 検証の区別

- 実Notebookのlive Acceptanceはユーザー指定KAIGO091の既存READY動画を使用。
  ON/OFFをcopy stateで回収し、同一remote動画を保持。新規Cloud生成は行っていない。
- EXEのCloud/Quota/Recovery試験はisolated fixture、Localメディア変換は実FFmpeg。
  Current Window試験は試験専用の通常Chromeで行い、本番profileには操作しない。
- 既知事項`SOURCE_UPLOAD_ERROR_LIVE_RECOVERY: UNVERIFIED_LIVE`は従来どおり保持。
  告知modalの自然再現なしをlive成功と数えず、fixtureで担保し、強制再現しない。

## 最終EXE / cleanup Gate

- EXE: ON/OFF×Ending有無4通りPASS。OFF converter call 0 / ZIP作成0、RAW不変、MP4保持、COMPLETED、TXT MOVED。
- limit/dual GUI、recovery、JobStateSave isolation、Stop/Close、sequential各smoke PASS。
- 実時間待機: **600.129938秒**後に再探索。Pause/Resume、USER_STOP、ALL_TASKS_COMPLETED、APP_CLOSE、両GUI停止理由表示PASS。
- Current Window: 専用通常Chromeの現在open/closed状態を検出。逆の過去履歴を与えても判定に使わない。Google操作0。
- GUI選択: PHASE2→次processでPHASE2復元→PHASE1→次processでPHASE1復元PASS。
- 新規日本語＋空白展開先: GUI、settings write/read、browser、preset、Fake E2E PASS。
  同path配下の`optional runtime`で実FFmpeg ON/OFF×Ending有無の4通りもPASS。
  初回PowerShell補助scriptでruntime名の文字化けを認めたため、UTF-8 JSONから正しい展開先を取得して再試験した。
  アプリsourceの不具合ではなく、正式EXE/sourceは再変更していない。
- 検証記録24件と最終821 testsを機械集計しPASS。
- cleanup: 旧clean Ver1.2.8 portable 1個と旧配布ZIP 4個、計412ファイルを削除。
  対象容量1,812,933,113 bytes→0、同量削減（約1.69 GiB）、失敗0。
  `Remove-Item -LiteralPath`を通常実行し、拒否や迂回なし。
  削除物はGit sourceと既存build scriptから再生成可能。ごみ箱への移動ではない。
- cleanup後: dist 979,424,934 bytes / build 458,271,389 bytes。
- 保持: Ver2.0 portable/ZIP/SHA、source/Git/history/main/phase2、元3repo、全本番JSON・実RAW/HLS/ZIP/MP4/TXT、
  settings/presets/browser profile、過去checksum/release文書。
  runtime/profile入り旧試験展開先はユーザーデータを保護するため一括削除しない。
- cleanup前後の保護対象2,995ファイルhash照合は変更0。
- main基準: `d81b625028b062ef392b5a0a4d920f2e4a51affa`。
  phase2: `f6f5871925f44718f51a106f7f55fa44e8f882b1`（変更0）。元3repo Git status clean。
- 全Gate PASS後にこの変更のみを通常commit・main通常pushする。push後SHA/0-0/cleanの結果は
  `work/DJD-CODEX-20260922-V20-HLS-ZIP-OPTIONAL-001.txt`へ記録する。force/rebase/reset/amendは使用しない。
