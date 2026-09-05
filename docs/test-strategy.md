# Unit 0: テスト戦略

## Unit tests

- state machineの全許可/拒否遷移、serialization round-trip
- JSON atomic replace、UTF-8、旧file保持、破損JSONの隔離
- remote削除gateの各1項目failと全項目pass
- TXT stemからNotebook名、path衝突、RAW上書き拒否
- 最終音声 + 0.5秒、全編無音、解析失敗、duration境界
- HLS playlist参照、連番、0 byte、ENDLIST、ZIP entry/CRC
- secret redactionと `.gitignore` safety pattern

## Integration tests

- fake `NotebookEngine` でsubmit → poll → download → RAW verifyをclock注入で実行（実際に10分待たない）
- saved job JSONからprocess再起動を模擬し、各checkpointで二重submit/remote削除が起きないことを検証
- fixture MP4をffmpegで生成し、末尾silence、0.5秒cut、Ending、HLS/ZIPを通す
- job AをNotebook待ちに置いたままjob BがEnding/HLS完了できることを検証
- 1件失敗しても他jobが完了するfailure isolation

## Negative / safety tests

- 0 byte、非MP4、header偽装、truncated MP4、audioなし、videoなし、duration 0
- download中size変化、`.crdownload`、size不一致、RAW copy failure
- safety gate 12項目のいずれかがfalseならremote delete mockが未呼出
- corrupt JSON、process強制終了直前/直後、stale temporary files
- malicious playlist (`..`/absolute path)、missing TS、0 byte TS、欠番、ENDLISTなし
- cancel時にRAWと既存outputが変化しないこと

実NotebookLM testは大量生成を行わず、DOM fixtureを基本とする。人手承認した保守testのみ既存完成artifactまたは1件を対象にし、認証/CAPTCHAを迂回しない。

