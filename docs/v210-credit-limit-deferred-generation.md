# Ver2.10：動画生成クレジット保留

指示: DJD-CHAPPY-V210-CREDIT-LIMIT-DEFERRED-GENERATION-FULL-001

Ver2.9 / 6084164を基準とする。source/live Gate後にPython/Product 2.10.0、FileVersion 2.10.0.0、GUI Ver2.10へ昇格した。

## 原因と変更

Ver2.9の`owned_video_dialog`は「生成」ボタンを必須としたため、同じ設定画面が「後で生成」に変わると既知の設定画面として認識できなかった。また従来の上限回復確認はChat利用可否であり、動画生成クレジットの現在状態を検証していなかった。

動画設定の見出し・言語・動画形式を確認し、下部`mat-dialog-actions`内のrole=buttonと完全一致accessible nameで操作状態を分類する。ユーザーから「少し回復して両方表示」との追加情報があり、実Notebookでも説明動画/日本語の設定で両方有効と確認した。有効な「生成」があればGENERATE_AVAILABLEを優先する。「生成」がない・無効かつ「後で生成」が見える場合だけ非エラーの`CLOUD_CREDIT_LIMIT_REACHED`へ変換する。「後で生成」の押下は行わない。

対象設定画面を一意に確認したうえで、明確なClose controlを1回だけ押す。bounded観測でdialog非表示、他dialog・scrim消失、Studio表示、動画解説controlのtrial click成功を確認する。不明・認証・削除確認等を自動で閉じる対象にはしない。安全に閉じられなければ`MODAL_CLOSE_NEEDS_REVIEW`で既存の停止処理へ渡す。

新規生成だけを保留し、既存のLocal、Artifact、Download、保存復旧の優先順位を維持する。生成上限そのものではChat/Source upload/回収/Download/LocalのCapabilityを落とさない。併存する実Chat上限は独立して観測し、CAN_CHATへ反映する。実操作の認証・アクセス・RAW gateは従来通り必要。

## 回復確認

`system/cloud-limit.json`へ上限signalと`last_credit_probe_at`、`next_credit_probe_at=now+600秒`をatomic保存する。回復時刻を推測せず、古いChat/Quota時刻を採用しない。再起動では保存予定を読み、期限経過・不正な期限なら安全な境界で再確認する。

Source READY・生成未開始の既存Notebookを優先し、現在のArtifact/Source状態を確認してStudio設定を開く。ProbeではNotebook作成、upload、Preset送信、設定変更、生成クリックを行わない。有効な「生成」を確認できた場合だけ上限を解除し、Probe結果を永続化する。disabled/unknown/一時失敗では解除せず再確認を予定する。認証切れ・未知modal・Stopは既存の処理へ渡す。

Local処理の実行中は安全なcheckpointを待つ。Download中は`CREDIT_PROBE`を既存Deferred Task Queueへ登録し、同一keyを重複登録しない。実Download完了後のscheduler境界でProbeする。回復後は通常のGeneration Dispatchが元のJob snapshotで生成を優先し、生成中・完成済みのJobは再生成しない。

QueueのProbe項目はprocess内のwake-upだけであり、再起動時は永続cloud-limit.jsonの期限から復元する。consumerのない古いqueue項目が完了判定を永続的に妨げないようにする。

## 検証

- 最終ソース1190件PASS（既存1129 + 新規61）。`work/v210/release-final.xml`を参照。
- 新規テストは実Chromium DOM、600秒境界、永続化、Download保護、優先順位、retry不消費、重複生成防止、両GUI、実Chat制限との分離を対象とする。
- `work/v210/source-acceptance-latest.json`: 隔離Chromium＋実Qt、6項目PASS。実Notebookの証跡とは区別する。
- 既存観測HTMLは変更しない。通常fixtureでは実UI通り両buttonを保持し、上限fixtureでは「生成」を除去する。併存時の有効/無効も個別にテストする。
- 本番保護対象790ファイルのhashを記録し、不変を確認。Notebook/Source/Artifact/RAW/成果物を削除していない。

実Notebookで両buttonのrole/name/enabled、説明動画/日本語、Close control、閉鎖後dialog/backdrop 0とStudio操作可能を確認した（work/v210/live-ui.json）。その後「後で生成」だけになる実上限画面も確認した。生成0・予約0で閉じ、アニメーション終了までboundedに待ってから閉鎖成功とした。

保存した期限から637秒後に、CUAと隔離状態ファイルを用いた実UI Probeを実施した。上限継続を確認し、生成0、upload0、Preset送信0、Notebook作成0で閉じ、次回を600秒後へ保存した（work/v210/live-probe-result.json）。自動schedulerの600秒境界・queue連携は別途制御fixtureで検証した。

回復済み実UIでは専用試験Notebook `7cf0db21-1b67-413d-a59a-d2b1a8b1e42a` を1件作成し、合成教材「分数の足し算」だけを追加した。説明動画・日本語・ペーパークラフト・Producer OFFを読み戻し、隔離Jobのclaim保存後、通常生成を1回だけ実施。完成動画1件（5:49、再生buttonあり）を確認し、Notebook・Source・Artifactを保持した。状態はwork/v210/live-job配下だけに保存し、本番job JSONは変更していない。ファイル追加は接続extensionのfile accessが未許可だったため、権限を変更せずNotebook標準のテキスト追加を使用した。

上限→回復の連続した自然Live遷移は試験中に起きておらず、遷移そのものは制御fixture/injectionとして記録する。実UIでの生成可能判定・生成開始・完成確認と混同しない。

配布物に試験用HTMLを含めない。EXE受入用の隔離コピーにだけ、Git管理の無害化済みHTMLを追加する。正式EXEのhashはコピーと照合し、通常portable/ZIPの検証はHTMLを追加せず実施する。

## 配布Gate

最終EXEの15系統（v210、v29、v28、schema、raw-storage、studio-direct、human-modal、download-completion、shutdown、recovery、limit-dual、lifecycle、sequential、save-isolation、hls-optional）はすべてPASS。日本語・空白pathのportable copyと、正式ZIPを実際に展開したportableのGUI、設定再起動、browser、Preset、Fake E2EもPASSした。

正式配布物は409ファイル。本番JSON、ユーザーruntime、認証情報、profile、fixture HTML、試験メディアの混入は0、ZIP CRCもPASS。

- ZIP: DJDmaker_Ver2.10.zip / 278,140,364 bytes
- ZIP SHA-256: `e3aad742ef01ee3bb6d48cdd6b45ce43ee8c61735312260d73d3d2e25531f4f8`
- EXE SHA-256: `c23e86218ce9ac2ab27dc307f4f63db5c0c15521b75e79ed0976fdd8caf179fa`

旧公開Ver2.9の再生成元は`6084164f6e7a2c762701d23e4a37eba4d4b7e137`。旧ZIP SHA-256は`397db58a0110badd81bad8506c5c81695ed6f2b0218d050a4035929231479070`、旧EXEは`bb2a736099b92c8e8d865cee616369e938767edc159a35065521655461adf9d9`。そのsourceのpackaging/build_windows.ps1と保持したレビュー済みFFmpeg 9.0.1・ffprobe・ライセンスから再buildできる。旧公開物はこれらのSHA、archive CRC、全fileの一致とruntime空を確認したうえで整理し、work/v210にprovenanceとmanifestを残す。Git履歴、source、ユーザーのDropbox配備先、settings、profile、RAW、完成成果物は保持する。

整理結果は24,735ファイル、7,919ディレクトリ、5,944,241,571 bytes。削除失敗0、readonly等の保持項目0、本番データ削除0。属性・ACL・policyは変更していない。最終distはVer2.10 folder・ZIP・SHAだけを保持する。
