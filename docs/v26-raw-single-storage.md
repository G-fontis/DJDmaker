# Ver2.6 RAW保存先の一本化

RAWの正本は設定画面の「RAW保存先」にある `<台本名>.mp4` だけです。Jobのraw_path、再起動時の照合、末尾カット・Ending・Encodeはこの正本を参照します。workは工程レポートや変換済み中間成果物に引き続き使用します。

## 旧経路と原因

旧経路はNotebook Downloadを `work/<job-id>/download/<台本名>.mp4` へ保存し、品質確認後にRawSafeStore.saveでユーザーRAW保存先へコピーする構成でした。Download側ファイルが残るため二重保存になっていました。保存リトライはさらにworkのmanual-sessionにraw.mp4をコピーし、旧RAWのrollbackコピーもworkへ作っていました。

## 新しい保存経路

Downloadの一時ファイルは `<RAW保存先>/.djd_download_tmp/<job-id>/session-N/` に置きます。Playwright Downloadの完了、サイズ安定、ffprobe、video stream、durationと3σ品質判定を通してから、同じRAWボリューム内で正式ファイル名へ公開します。通常公開は同一ボリュームのatomic no-overwrite linkと一時名の除去で、動画データを複製しません。競合時は既存内容を検証し、異なる正常RAWを上書きしません。

公開前にDownloadのSHA-256をJobへ保存します。公開直後に落ちても、そのSHAと正式RAWが一致する場合だけ所有権を復旧できます。ファイルの存在だけでは採用しません。品質判定待ち・初回＋最大5回のretry・一時拡張子拒否・Download Guard・Deferred Queueは維持します。

保存リトライは新候補とrollback RAWをRAW保存先の配下で扱います。検証済み候補から作る変換済み成果物のステージ先は出力先配下です。RAWと完成成果物の置換に失敗した場合は元のRAW／成果物へ戻します。成功後はRAWの候補名・rollbackファイルを残しません。正常RAWを先に削除する工程はありません。

次の明示リトライでは、そのJobの前回sessionに属する一時候補だけを整理します。正本・rollback・旧work RAWは整理対象にしません。上限待機中のローカル復旧は、旧work Downloadの有無に依存せず、保存済みpath又はSHAに一致する正本RAWを読み直して進めます。

## 旧Jobの回復

新Jobはraw_storage_version=2、旧JSONの欠落値は1として読みます。旧RAWを探すのはversion=1の明示的な移行処理だけです。候補は保存されたJob IDと台本名に一致する旧download path、又はそのJobのmanual-session/raw.mp4です。保存済みraw_path又はDOWNLOADED checkpointとの対応、他Jobとの非共有を確認し、media／品質を検証して、正本がない場合だけ指定RAW先へコピーします。

既存の正本があれば旧work RAWを入力として使用しません。移行後はraw_pathと、RAWを兼用していたedited_pathを更新します。旧work RAWは削除しません。新Jobはwork RAWへfallbackせず、ユーザーRAWがない場合は回収・確認へ戻ります。

## GUIと保持する機能

Job詳細は正本RAWを表示し、未移行の旧work参照は「旧RAWの移行待ち」と表示します。PHASE2の容量表示は指定RAW先のボリュームを参照します。HLS/ZIP ON/OFF、Ending／末尾カット／Encodeの独立設定、Studio Direct、未知モーダルの人間確認、Current Window、保存失敗隔離は変更しません。

## 実Notebook検証

2026-10-18 JST、Ver2.5で作成した検証Notebookの動画を製品Pipelineで回収しました。指定した隔離RAW先で3σ判定PASS（0.092244141605 MiB/sec、下限0.038192558112）、RAW本体1、Pipeline work内の同一SHA RAW 0、末尾カット入力が正本RAWであることを確認し、Completedまで到達しました。RAW SHA-256は `a8673c93253e6e86f0e9754e8326e919e8dbd42e2174ae0829d4b5db32a3397b` です。Notebook・Source・artifactの削除はありません。

証跡はwork/v26-live-02/result.jsonに保存します。work/v26-live-02は受け入れ証跡の隔離ルートで、その中のuser-rawとPipelineに指定したpipeline-workは別の保存先です。配布フォルダにはこれらの動画・profile・Jobを含めません。

最終全体テストは1021件PASS（既存994＋新規27）。最終EXEではRAW保存・保存リトライ・HLS ON/OFFを含む11系統、両GUIの再起動を確認しました。ポータブル検証の初回にはffprobe失敗を検出しましたが、RAW単独再検査と、他の検証終了後の新品コピーでの単独再実行はPASSしました。正式配布フォルダは実行せず、profile・Job・動画・ログのない状態を監査しています。
