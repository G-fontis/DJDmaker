# Unit 4 元エンジン忠実性・ログ・安全監査

監査日: 2026-09-06
対象: 読み取り専用。元repositoryへの変更、live NotebookLM操作、commit、pushは行っていない。

## 監査基準点

| Repository | Branch | HEAD | Worktree |
|---|---|---|---|
| AutoGeminiNoteBookCreator | `main` | `28bd51dfe2894018bfc9d65a02f219a933199127` | clean |
| GeminiNotebookVideoMerge（ドウガッチンガー） | `main` | `242a8dc9a4cf33badae9882f5239d3886b8943d8` | clean |
| FukuzemiApp（HLS Converter） | `main` | `434e70c23eafc58ade531ec24601198db0687248` | clean |

## 比較結果

| 観点 | 元エンジンの確認結果 | DJDmakerの採用状態 | 判定 |
|---|---|---|---|
| multi-job identity | GNBはSQLite job ID、Notebook ID/URL、TXT stemを併用。ドウガッチンガーは `job_id + normalized output path` をretry identityに使用。HLS Converterは独立IDを持たずsource pathとtimestamp付き出力名で識別 | 永続UUID `Job.id`、Notebook ID/URL、TXT stemを分離。work checkpointは `work/<job-id>/...` | PASS |
| completion wait | GNBは生成開始を2秒poll、120秒単位で観測し、生成中・予約・完成・remote失敗を区別。完成は動画カードの「再生」で判定 | 初回600秒、以後120秒の永続deadline。`WAITING_VIDEO`だけpollし、deadlineをremote I/O前に保存 | PASS |
| download target | GNBはtarget stem、明示title hint、sole playable card、sole cardの順。ただし複数候補は停止 | artifact title一致またはsole playable videoへ限定。download先は `work/<job-id>/download/<stem>.mp4` | PASS（DJDmakerは非playable sole card fallbackを採用しない） |
| artifact deletion | GNB本体の自動削除は検証後のNotebook全体削除。保存診断 `STATE_10_VIDEO_MENU` は動画カード内 `その他` とartifact menuの `ダウンロード`・`削除` を実証 | 12項目gate後だけ、playable card内 `その他` を開き、`ダウンロード` markerを持つartifact menuだけで `削除`。Notebook文言dialogは拒否し、refresh後の非再出現まで確認。Notebook削除APIなし | PASS / 安全側差分 |
| FFmpeg lane | GNBはbrowser中心。ドウガッチンガーのmulti-job coordinatorとHLS QueueWorkerは逐次処理 | Notebook laneは直列、media laneだけ `ThreadPoolExecutor`。設定値は1または2、既定1 | PASS（bounded parallel） |
| failure isolation | ドウガッチンガーは出力job単位retry、HLSは失敗job後も次jobを継続 | media job例外をjob単位FAILEDへ閉じ込める。remote cleanup失敗後も検証済みRAWと後段進捗を保持 | PASS |
| log correlation | GNBはjob ID/Notebook IDのaudit contextとsource名、ドウガッチンガーはoutput job ID/path/retry identity、HLSはinput path中心 | state logへstable `job_id` と `script_name` fieldを格納し、messageにも `[stem]` を表示 | PASS（Unit 4補強） |
| portable | GNBはexe隣接writable root、`user_data/chrome_profile`、同梱`bin/ffprobe.exe`。ドウガッチンガーは`runtime/ffmpeg`候補。HLSはonedirでexe基準 `tools/ffmpeg[/bin]`、設定/logはLocalAppData | exe配置directoryをrootとし、`runtime/ffmpeg`、`tools/ffmpeg/bin`、PATHの順でmedia toolを探索。browser/log/system/raw/output/workはroot配下のwritable領域 | PASS（配布前preflight必須） |

## 主要な証跡

### AutoGeminiNoteBookCreator

- `app/automation/video_monitor.py`: artifact件数、再生button、active/scheduled/failed marker、120秒生成開始観測。
- `app/automation/download_manager.py`: stem/title hint/playable cardによるdownload scope、temporary download、検証後publish。
- `app/automation/notebook_deletion.py`: 実装済み削除はNotebook全体であり、artifact削除ではない。
- `diagnostics/20260905_101556/STATE_10_VIDEO_MENU`: live UI上の動画card `再生`、card内 `その他`、menuitem `ダウンロード` と `削除`。
- `app/runtime_paths.py`: portable root、browser profile、log/diagnostic、bundled ffprobe配置。

### GeminiNotebookVideoMerge

- `app/job_retry_policy.py`: `job_id` と正規化output pathによる衝突しないretry identity。
- `app/multi_job_processing.py`: output jobを順次実行し、job単位の成功・失敗・retry metadataをJSONLへ渡す。
- `app/services/audio_tail_service.py`: 末尾30秒から倍増する逆方向探索。
- `app/services/ffmpeg_service.py`: job staging、ffmpeg/ffprobe探索、atomic output方針。

### FukuzemiApp

- `hls_converter/worker.py`: 1 worker内でjobを順番に処理し、失敗を次jobから隔離。
- `hls_converter/ffmpeg.py`: H.264/AAC、約6秒segment、`playlist.m3u8`、`segment%05d.ts`。
- `hls_converter/archive.py`: `ZIP_STORED` archive作成。
- `README.md`: PyInstaller onedir、exe基準のtool探索、LocalAppDataへの設定/log保存。

## Unit 4で追加した独立検証

- `tests/test_unit4_engine_fidelity.py`
  - `ffmpeg_concurrency=2`で2つのjobが実際に同時にEndingへ入ること。
  - 各jobのcheckpointが別々の `work/<job-id>/ending` に置かれること。
  - 状態logがstable job IDと人が読めるTXT stemの両方を持つこと。

## 安全監査

- tracked runtime artifactは各directoryの `.gitkeep` のみ。MP4、HLS、ZIP、browser profile、log、job JSON、DBは追跡されていない。
- 高確度のAPI key/private key/token形式はtracked fileから検出されなかった。
- production sourceにSQLite、SQLAlchemy、DB filename参照はない。DB suffixは安全テストの禁止一覧にだけ存在する。
- `.gitignore`で `.env`、credential、token、cookie、browser、logs、system job JSON、raw media、output、work、DBを除外することを `git check-ignore` で確認した。
- remote artifact削除は12項目gateをすべて要求し、Notebook削除へ到達するinterfaceを持たない。

## 注意事項

- 同名TXT stemのRAW/最終ZIPは仕様どおり衝突停止する。内部checkpointはjob IDで分離されるが、公開成果物を自動改名・上書きしない。
- FFmpeg同時数2は性能保証ではなく上限設定である。既定1を維持し、端末性能を確認した場合だけ2を選ぶ。
- GNBのartifact削除確認dialogは保存診断に存在しないため、DJDmakerはdialogなし/ありの両方を扱うが、Notebook文言を含むdialogを決して確定しない。
- 配布物は `python -m djd_maker.packaging.preflight <root>` の全required check通過後のみ候補とする。
