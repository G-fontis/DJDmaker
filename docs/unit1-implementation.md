# Unit 1 実装記録

指示ID: `DJD-CHAPPY-V01-UNIT1-CORE-ADAPTER-PIPELINE-001`

## 実装範囲

- `core/repositories.py`: schema v1付きSettings/Job/Queue/State JSON repository、atomic replace、更新前backup、temporary/backup recovery、corrupt隔離、thread/process lock、unclean shutdown検出。
- `media/validator.py`: regular file、temporary suffix、0 byte、size stability、ffprobe、video/audio stream、duration検証。
- `media/raw_store.py`: download再検証、同一directory staging、fsync、hard-link no-overwrite publish、RAW側再検証、12項目remote delete gate。crash後の既存RAWはread-onlyで再照合する。
- `adapters/ending.py`: 末尾30秒から逆方向へ `silencedetect`、最終音声 + 0.5秒、全編無音/no audio/解析失敗時は本編全長維持、固定Ending、staging、ffprobe、collision拒否。
- `adapters/hls.py`: H.264/AAC、約6秒、VOD playlist、`segment%05d.ts`、HLS/codec検証、ZIP_STORED、flat entry/CRC/0 byte検証、atomic no-overwrite publish。
- `adapters/notebook.py`: create、TXT upload/source ready、TXT stemへのrename、generation、DOM status、download handoff、artifact-only delete gate。Notebook削除APIは存在しない。
- `orchestration/pipeline.py`: serialized Notebook laneとbounded FFmpeg lane（1〜2）、job failure isolation、restart checkpoint、download/remote cleanup retry。
- `testing/fake_notebook.py`: 実Geminiへ接続しない完成artifact fixture。

## 再開ポリシー

- `COMPLETED` / `FAILED`: 自動再実行しない。
- `UPLOADING`: remote作成済みか不明なため `SUBMISSION_STATE_UNCERTAIN` でfail-closed。重複Notebookを自動作成しない。
- `GENERATING`: remote ID/URLがあれば `WAITING_VIDEO`、なければfail-closed。
- `DOWNLOADING`: `WAITING_VIDEO`へ戻し、既存download/RAWが同sizeかつ双方ffprobe合格なら再downloadせず `RAW_READY`へ復旧。
- `ENDING`: `RAW_READY`、`HLS_ENCODING`: `ENDING`、`ZIPPING`: `HLS_ENCODING`へ戻し、検証済みcheckpointは再利用する。
- `DOWNLOAD_VERIFY_FAILED`: remote artifactを保持し、明示的な再回収だけを許可する。

## NotebookLM DOM確認

GNBCreatorのHEAD `28bd51dfe2894018bfc9d65a02f219a933199127` に、2026-09-05の日本語画面で確認されたcreate/title/source/video/status/download selectorがあることを確認した。Unit 1では実アカウントによる追加生成やlive DOM操作は実施していない。

元GNBCreatorの削除処理はNotebook全体を削除するため移植していない。動画artifact専用delete selector/dialogは未確認であり、`ArtifactDeleteSelectors` が明示注入されない限り `ArtifactDeletionDisabled` として削除しない。通常pipelineからNotebookを削除するmethodは呼べず、adapter/interface/Fakeのいずれにも `delete_notebook` は存在しない。

## Test evidence

headless E2Eは動的生成した3秒MP4をFake Notebook完成動画としてdownloadし、RAW保存、最終音声 + 0.5秒cut、固定Ending、HLS、ZIP、`COMPLETED`まで実FFmpeg/ffprobeで確認する。

failure testsは0 byte/corrupt/temporary、RAW collision、Ending missing/corrupt/collision、very short/no audio/長い末尾無音、HLS不正playlist/segment/codec、ZIP failure/collision、malformed JSON、crash recovery、illegal transition、1 job failure isolation、remote delete gate、remote delete failure after RAWを対象とする。

## 次Unit

PySide6 GUI、clock/scheduler（初回600秒・以後120秒）、live DOM診断、動画artifact専用delete controlの人間確認、packaging、Tcl/Tk/PySide runtime確認を行う。artifact削除はlive確認と専用fixtureがPASSするまで有効化しない。
