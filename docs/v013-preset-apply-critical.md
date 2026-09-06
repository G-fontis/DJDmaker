# v0.1.3 preset apply critical fix

指示ID: `DJD-CHAPPY-V013-CRITICAL-PRESET-APPLY-MAIN-001`

実施日: 2026-09-06

開始HEAD: `0c5f8898bc29eabe613be2ca594475864046a978`

開始時origin/main: `e9982c4f1d4dfc8ff3e075d969ff9fe578926ba3`

## Root cause

`build_desktop()`のpipeline factoryは、その時点のGUI選択presetを正しく取得していた。しかし`GuiPipelineController`が自然終了済みの`PipelineCoordinator`を次のStartでも再利用していたため、同一アプリ内の2回目以降は最初のStart時のpresetとbrowser pageが残った。preset IDが同じまま本文だけ編集した場合も旧本文を使う、事故経路E/F/Gに相当する。

以前のAcceptanceで使った内部識別文は、外部のAcceptance用user-data `system/presets.json`とjob JSONだけに存在した。production source、config、default、正式packageにfallbackとして存在しない。production adapterが参照する本文はjob snapshotだけに限定した。

## Fixed flow

1. アプリ起動時の選択は常に空。preset一覧だけを永続化し、前回の`selected_preset_id`は無視する。次回preset保存時に互換fieldをnullへ移行する。
2. Start時にpreset未選択ならGUIで明示エラーにし、Notebook作成前に停止する。pipeline側も`PRESET_NOT_SELECTED`でfail closedする。
3. 新しいStartごとにpipelineを再構成し、その時点の選択presetを取得する。
4. WAITING jobへ`preset_id`、`preset_name`、`preset_body_snapshot`、`preset_body_sha256`を保存する。
5. Notebook adapterは`preset_body_snapshot`だけを読み、互換用`generation_prompt`やtest promptへfallbackしない。
6. custom topicへfill後、`input_value()`をreadbackする。expectedと完全一致した場合だけGenerateをクリックする。不一致またはreadback失敗は`PRESET_APPLY_MISMATCH`で停止する。
7. 開始済みjobはsnapshotを保持し、同じpreset IDの本文編集や別preset選択の影響を受けない。

## Verification

- 全test: `260 passed`
- compileall: PASS
- `git diff --check`: PASS
- production/test prompt構造分離test: PASS
- portable smoke: GUI、settings save/restart、browser、preset CRUD、起動後selection reset、Fake E2E、FFmpeg/ffprobeすべてPASS
- 正式portable: `dist/DJDmaker_v0.1.3`
- 正式portableのruntime/profile/media/settings files: 0
- EXE: 3,783,011 bytes
- EXE SHA-256: `86EC71F12F7EF792363352204E1758B215757C71ECE048B61433A4F2D9E06A1A`
- FileVersion: `0.1.3.0`
- ProductVersion: `0.1.3`

## Portable live preset acceptance

同一portableアプリでStartとStopを繰り返し、新Startごとのpipeline再構成を含めて確認した。3件は別Notebookで、各job JSONのpreset ID/name/snapshot/hashが選択内容と一致した。`WAITING_VIDEO`へ遷移したことにより、DOM readback完全一致後にGenerateがクリックされたことを確認した。

| job | preset ID | snapshot SHA-256 | Notebook ID | result |
|---|---|---|---|---|
| A | `5abdc32969c7421796bc839d94681e8e` | `43b82421240fe8332782093d7dd5540e494ed12e56b679c10dff5b4cd60a6aa5` | `b3e8982e-4c23-4408-9c95-a5ff15db4742` | DOM一致、生成開始 |
| B | `923439b71fb0447694fb9db227194798` | `b9a26b3edca6caa8cdc5904eb10a680ec2e204a4f4964bcfe643ae1806438d83` | `2b090397-2bd9-43eb-bb8a-368f9df9f635` | DOM一致、生成開始 |
| C | `ab4e355836c84645aadeec3a5c57a382` | `0e0cb981b703dd97fc0988e11aeff68b8cb27f7aba168d2c604d21d708a13ae5` | `ff6f2d48-7495-4d4e-a02a-3f78e2683854` | DOM一致、生成開始 |

内部Acceptance presetの混入は0件、A/B/C間のID・hash・Notebook取り違えも0件だった。完成検出以降は既存v0.1.3 live E2Eで合格済みであり、ユーザー指示により重複する新規完走を省略して安全停止した。既存E2EのRAW、Notebook、sourceは変更・削除していない。

