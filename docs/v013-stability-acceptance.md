# v0.1.3 Stability-first real-use acceptance

指示ID: `DJD-CHAPPY-V013-STABILITY-FIRST-REAL-USE-ACCEPTANCE-001`

実施日: 2026-09-06

対象source HEAD: `8da310504a2a81f7c6a43be378dc0dc0a6d2572b`

## Fresh portable

`dist/DJDmaker_v0.1.3`を、既存runtime/profileを含まない新規のDropbox配下・日本語・空白pathへcopyして使用した。初回起動前のChrome profile fileは0件だった。初回GUI起動、writable runtime directory生成、settings保存、正常終了を確認した。

Preset Aを新規登録、編集、複製、複製を削除した後、Preset Bを登録・選択した。repository再生成とportable再起動後もPreset BのID・本文を復元した。live Notebookのcustom topic欄へA、Bを順にfillし、`input_value()`で双方の完全一致を確認した。

AUTHは通常Chrome、remote debugging 0、automation flag 0でFresh Google loginを実施した。AUTH Chrome終了後、profile lockなし、同profileを使うChrome 0を確認してStartした。automation Chromeは同じprofile/sessionを再利用し、Pre-flight、Gemini遷移、home DOM、required selectorsを通過した。security rejectionは0。正常時の人間操作はGoogleログインとStartの2工程で、Start後の追加操作は0だった。

## Single live E2E

`DJD_STABILITY_SINGLE_001.txt`はNotebook `2e22aee7-0eda-4b4d-83e8-e5b9b7d7c0d2`で完走した。Preset BのID、名前、本文はjob JSONへsnapshotされ、Notebook生成へ送信された。

- state: `COMPLETED`、Error 0
- RAW: 9,128,093 bytes、66.200091秒、H.264/AAC
- RAW SHA-256: `EA5222F3E9495AAF3382C03A6A8D2CFE1B1910AD41E13BEDE2FDA027A2DA379F`
- download/RAW safety gate: 12/12 PASS
- artifact: Web UI削除後、refresh済みlive DOMで0件
- Notebook/source: 保持
- Ending/HLS: PASS
- ZIP: 5,558,027 bytes、10 entries、`testzip() is None`、全entry `ZIP_STORED`

## Three-job live E2E

3件は同じPreset B snapshotで開始した。完成順は002、001、003だったが、UUID、Notebook URL、TXT stem、RAW stem、ZIP stemは全件一致し、取り違えは0だった。

| TXT | Notebook ID | RAW bytes | duration | RAW SHA-256 | ZIP bytes/entries |
|---|---|---:|---:|---|---:|
| `STABILITY_DJD_MULTI_001` | `094e23c8-4171-4149-9cfb-0ee019e13112` | 2,661,095 | 50.201542 | `90EE238B4674BEA9ABFA9220831334CDF69A5783C0DCB08392A755732D858B5C` | 1,496,527 / 5 |
| `STABILITY_DJD_MULTI_002` | `6c09f347-4a1e-43a4-83a5-5f9e50923d08` | 2,668,419 | 62.786757 | `4CF8ABBBBE406714E2C0B8795A0E4BAA3D1FEBF871D138FA7A68BFB4F54EB8E0` | 2,146,675 / 8 |
| `STABILITY_DJD_MULTI_003` | `1cbaf8ae-96c5-40af-88c6-6fe1735dd327` | 2,132,220 | 69.450884 | `DDA22BC2149DBD22E633955B5280A98610B43E337395ABE9C05AA9C62D300AEA` | 1,101,397 / 4 |

全件12/12 safety gate、H.264/AAC、Ending/HLS、ZIP CRC、`ZIP_STORED`をPASSした。各Notebookを再訪し、title/source一致、動画artifact 0、Notebook/source保持を確認した。全件`COMPLETED`、Error 0、artifact誤削除0、RAW変更0。

## Recovery、UX、回帰

隔離したcontrolled testで`WAITING_VIDEO`、`RAW_READY`、`HLS_ENCODING`から復帰し、保存済みNotebook identity、RAW、Ending checkpointを再利用した。COMPLETED job、既存RAW、既存ZIPは再処理・上書きしなかった。portableを正常終了して再起動し、settings、Preset B選択、4件の`COMPLETED`を復元した。

開始後preset編集のsnapshot不変と、新jobだけが更新本文を使うtestを追加した。同じpresetを使う3 jobのsource/RAW/ZIP/artifact identity testも追加した。全suiteは249 passed。compileallと`git diff --check`もPASSした。

未選択preset、未認証、profile lock、Gemini navigation/DOM不適合、Ending未設定は、Notebook作成前に明示エラーで停止する回帰testを通過した。白画面・無反応は0。

元3repoの実運用機能分類は`docs/v013-stability-feature-inventory.md`に記録した。A=14、B=6、C=6、D=0で、必須未移植は0件。元3repoは変更していない。
