# Unit 0: 統合アーキテクチャ

## 方針

UI、orchestrator、3 engine adapter、JSON persistenceを分離する。元コードを丸ごと結合せず、`NotebookEngine`、`EndingEngine`、`HlsEngine` の小さい契約へ必要機能だけを移植する。`src/djd_maker/core/interfaces.py` がUnit 0の契約、`models.py` が状態と削除安全ゲート、`storage.py` がDB代替の原子的JSON保存である。

PySide6をGUI基盤とする。GNBCreatorと同じtoolkitを選ぶことでNotebook操作側のevent/threadモデルを再利用しやすくする。ドウガッチンガー/HLS ConverterのTk GUIは移植せず、serviceロジックだけをadapterへ取り込む。

## Pipeline

```text
TXT scan -> Notebook submit -> initial 10 min wait -> DOM poll every 2 min
                                                 |
                                      completed artifact
                                                 v
download quarantine -> stable/ffprobe -> copy RAW -> verify RAW -> remote delete gate
                                                 |
                                                 v
audio tail + 0.5s -> append fixed Ending -> verify MP4
                                                 |
                                                 v
HLS (~6s/H.264/AAC) -> verify HLS -> ZIP -> verify output
```

Notebook polling laneとFFmpeg laneを分離する。あるjobがNotebook待機中でも、`RAW_READY` になった別jobはbounded worker pool（初期値1）でEnding/HLSへ進める。job failureはそのjobのJSONへ閉じ込め、他jobを停止しない。ただし認証要求、CAPTCHA、selector不明、出力path衝突などglobal safety failureは新規投入を停止する。

## Filesystem

```text
project-root/
  input/                    private TXT
  raw_files/<job>.mp4       immutable source of record
  output/<job>.zip          final artifact
  work/<job-id>/
    download/               quarantine download
    ending/                 staged edited MP4
    hls/                    playlist and segments
  system/
    settings.json
    queue.json
    state.json
    jobs/<job-id>.json
  logs/<date>.jsonl
  browser/                  dedicated Chrome profile
```

全JSONは同一directory内のtemporary fileへflush/fsync後、`os.replace` する。jobごとのファイルに分け、1件の破損が全queueを失わせない。将来schema versionとmigrationを各JSONへ持たせる。RAW作成は同一filesystem上の一時名からexclusiveな最終名へ移し、既存RAWは上書きしない。

## Job state

基本遷移はコードの `ALLOWED_TRANSITIONS` でfail-closedに制限する。

```text
WAITING -> UPLOADING -> GENERATING -> WAITING_VIDEO -> DOWNLOADING
                                                     |          |
                                      DOWNLOAD_VERIFY_FAILED    v
                                                     <----- RAW_READY
                                                               |
                                      ENDING -> HLS_ENCODING -> ZIPPING -> COMPLETED
```

どのactive stateからも `FAILED` へ遷移できる。retryは失敗jobを直接巻き戻さず、元jobのattemptと再開checkpointを記録した新attemptとして扱う。`DOWNLOAD_VERIFY_FAILED` ではremote成果物を保持する。

再起動時のreconciliation:

| Persisted state | 再開前確認 | Resume point |
|---|---|---|
| UPLOADING / GENERATING | notebook URL/DOMとremote artifact | GENERATINGまたはWAITING_VIDEO。確証なしなら人手確認 |
| WAITING_VIDEO | notebook URL/DOM | pollを再登録 |
| DOWNLOADING | quarantine/RAW双方を再検証 | download再試行またはRAW_READY |
| DOWNLOAD_VERIFY_FAILED | remoteを削除せず診断情報を表示 | manual/re-download |
| RAW_READY / ENDING | RAWをffprobe、stagingを破棄して再作成 | ENDING |
| HLS_ENCODING / ZIPPING | edited MP4を検証、一時HLS/ZIPをjob内だけ再作成 | HLS_ENCODING |
| COMPLETED | ZIP存在・内容をread-only確認 | 完了維持、不整合ならFAILED |

## Remote deletion safety

`DownloadSafetyGate` の12項目がすべてtrueの場合だけremote削除adapterを呼べる。必須項目はdownload正常終了、一時拡張子でない、MP4存在、non-zero、size stable、ffprobe、video stream、duration > 0、RAW copy成功、RAW存在、size一致、RAW ffprobeである。結果はjob JSONへ証跡として保存する。

## Retry and failure isolation

- network/DOM poll: exponential backoffではなく仕様の2分pollを維持。瞬断のみ短い限定retry。
- download: 同じremote artifactを再取得。破損quarantineは診断用に保持し、RAWへ昇格しない。
- Ending/HLS: deterministicなためjob work directoryだけを再作成して再実行可能。
- JSON parse failure: 対象jobをquarantineし、他jobをloadする。
- stop/pause: pauseは新stage開始前のsafe point、stopはsubprocessへgraceful terminate後kill。RAWは常に保持。

