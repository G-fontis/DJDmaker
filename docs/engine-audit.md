# Unit 0: 3エンジン調査記録

調査日: 2026-09-06（Asia/Tokyo）

元コード参照前にrepository path、remote、branch、HEAD、dirty/cleanを記録した。その後 `git fetch --prune origin` を行い、3件ともローカルHEADと `origin/main` が一致（ahead/behind `0/0`）することを確認した。元repositoryのworktreeは変更していない。

## 元Git一覧

| Engine | Repository root | Remote | Branch | HEAD | Status |
|---|---|---|---|---|---|
| GNBCreator | `C:\xampp\htdocs\PHP\AutoGeminiNoteBookCreator` | `https://github.com/p11ichiro/AutoGeminiNoteBookCreator.git` | `main` | `28bd51dfe2894018bfc9d65a02f219a933199127` | clean |
| ドウガッチンガー | `C:\xampp\htdocs\PHP\GeminiNotebookVideoMerge` | `https://github.com/G-fontis/GeminiNotebookVideoMerge.git` | `main` | `242a8dc9a4cf33badae9882f5239d3886b8943d8` | clean |
| HLS Converter | `C:\xampp\htdocs\PHP\FukuzemiApp` | `https://github.com/G-fontis/FukuzemiVideoConverter.git` | `main` | `434e70c23eafc58ade531ec24601198db0687248` | clean |

## GNBCreator

確認できた実装:

- `app/automation/notebooklm_page.py`: Notebook作成、TXT upload、source ready待機、`set_notebook_title()`、prompt、Video Overview生成要求。タイトルは入力値/表示ラベルとdocument titleの双方をreadbackする。
- `app/automation/video_monitor.py`: artifact DOM、再生ボタン、生成中/予約/失敗markerによる直接状態判定。複数artifactを曖昧なまま処理しない。
- `app/automation/download_manager.py`: 対象artifactの一意特定、Playwright/CDP download、`.crdownload`除外、0 byte検査、一時名保存、ffprobe検証後のrename。
- `app/services/video_validator.py`: MP4拡張子、0 byte、`ftyp` header、ffprobe、video stream、duration > 0。
- `app/automation/notebook_deletion.py`: local動画再検証後、URLと対象行を一意確認してNotebookを削除。
- `app/automation/remote_recovery.py`, `batch_processor.py`: 生成依頼と回収の分離、再開、安全停止、一時停止。
- `app/config/selectors.py`, `browser_manager.py`, `source_monitor.py`, `limit_detector.py`: selector集約、専用Chrome profile、source状態、利用上限検出。

Notebook名変更は実装済み。download検証も実装済みだが、本統合仕様の削除ゲートに対しては次が不足する。

- 元実装は保存先動画を再検証してNotebook自体を削除する設計。本アプリでは「remote動画削除」の対象とDOM手順を再確認する必要がある。
- download完了後の一定時間size不変を通常経路で明示検証していない。
- 一時保存先から `raw_files` へのcopy成功、copy後size一致、copy後ffprobeを独立記録していない。
- job/preset/settingsはSQLiteに依存するため `app/db/*` と `JobService` は移植不可。DOM・download・validatorロジックをadapter化し、状態はJSONへ置換する。

再利用候補は `notebooklm_page.py`、`video_monitor.py`、`download_manager.py`、`video_validator.py`、`notebook_deletion.py`、`browser_manager.py`、`source_monitor.py`、`limit_detector.py`、`config/selectors.py`。Playwright selectorは外部UI変更に弱いため、診断fixtureとfail-closedテストを伴って移植する。

## ドウガッチンガー

正式対象repositoryはREADMEのアプリ名とVersion 3.0仕様から `GeminiNotebookVideoMerge` と特定した。

確認できた実装:

- `app/services/audio_tail_service.py`: FFmpeg `silencedetect=noise=-50dB:d=0.5`。末尾30秒から逆方向へ探索範囲を倍増し、最後の有効音声位置を求める。
- `app/audio_tail_policy.py`: `min(duration, last_audio_end + padding)`。padding初期値0.5秒。全編無音/解析失敗時は安全側で全長維持。
- `app/single_ending_policy.py`: MAIN 1本ごとの独立出力、eyecatch無視、選択Ending 1本を付与。
- `app/services/ffmpeg_service.py`: ffmpeg探索、clip作成、concat list、staging output、cancel、最終rename。
- `app/services/ffprobe_service.py`, `output_validation.py`: stream/metadata取得とduration toleranceを含む出力検証。
- `app/processing.py`: timeline、末尾解析、FFmpeg実行、検証のcoordinator。

テストは音声末尾の純粋ロジック、逆探索、実FFmpeg統合、Ending、ffmpeg/ffprobe、出力検証、単発Ending、cancel/retryを含む。

再利用候補は `audio_tail_policy.py`、`audio_tail_service.py`、`ffmpeg_service.py`、`ffprobe_service.py`、`output_validation.py` とsingle-endingのpolicy。統合時は複数結合、group、eyecatch、random機能を除外し、RAWと出力が同一pathにならないpreflightを追加する。

旧仕様との差分: 「末尾固定4秒削除」は採用しない。正式仕様どおり末尾から最終音声を探索し、最終音声 + 0.5秒でcutし、その後に固定Endingを1本だけ付ける。

## HLS Converter

正式対象repositoryはREADMEと `FUKUZEMI_HLS_CONVERTER_SPEC_v1.0.md` から `FukuzemiApp`（remote名 `FukuzemiVideoConverter`）と特定した。

確認できた実装:

- `hls_converter/ffmpeg.py`: ffmpeg/ffprobe探索、media probe、H.264 encoder、yuv420p、AAC 128k、`-hls_time 6`、VOD、`playlist.m3u8`、`segment%05d.ts`。
- `hls_converter/validation.py`: playlist存在/read、segment参照、相対path、存在、0 byte、連番欠落、`#EXT-X-ENDLIST`。
- `hls_converter/archive.py`: playlistとTSだけをZIP root直下へ `ZIP_STORED` で一時作成し、成功後rename。
- `hls_converter/worker.py`: probe → encode → HLS検証 → ZIP → 完了の処理とjobごとの失敗分離。

テストはcommand、6秒設定、playlist/segment検証、ZIP内容、実ffmpegが利用可能な場合の変換を含む。

再利用候補は `ffmpeg.py` のtool探索/probe/command、`validation.py`、`archive.py`、`profiles.py`。GUI/終了時Windows shutdownは統合しない。統合側ではZIPを再openしてentry集合・CRC・0 byteを検証し、HLS playlist自体のffprobe/decode smoke testを追加する。

## 既存テストの基準結果

- GNBCreator: `140 passed`。pytest cacheを書けない既存環境warningが1件あるが、test failureはなし。
- ドウガッチンガー: `1051 passed, 7 skipped, 2 failed`。失敗2件はいずれもGUI生成時にglobal Python 3.14側のTcl/Tk libraryが不完全なことによる `TclError`。音声末尾、Ending、ffmpeg/ffprobeを含む非GUIロジックは通過した。repositoryの既存venvにはruntime dependencyはあるがpytestがないため、global pytestからvenv site-packagesを参照して実行した。
- HLS Converter: `39 tests OK`。

この結果は元repositoryの現状調査であり、本repositoryのUnit 0合否とは分離する。3元repositoryのworktreeはテスト後もcleanである。
