# Ver1.0 critical function fix

## Preset / chat flow

旧DJDmakerは動画解説カードを開き、カスタムトピックへpresetをfillしてGenerateを直接押していた。この経路ではNotebookLMのdefault「短め」が選ばれるため、ユーザーの「形式：説明動画」が反映されなかった。

Ver1.0の通常pipelineは、source ready後にjobの`preset_body_snapshot`をNotebookLMメインチャットへ完全一致で入力する。DOM readback、送信ボタンの有効化、user messageの2秒安定表示、動画artifact生成開始を順に確認する。動画解説カード、カスタマイズ画面、Generateボタンは通常pipelineから呼ばない。旧カード操作コードは診断用として残す。

正式live試験文は次のとおり。

```text
ソースの読み込みが完了したら、以下の条件で動画を生成開始してください。
形式：説明動画
・日本語
・ペーパークラフト
```

2026-09-06の現行NotebookLMで、完全一致のuser message、Notebook応答「ペーパークラフトスタイルの日本語説明動画の生成を開始しました」、生成中artifact 1件を同一automation context内で確認した。ページDOM内の「短め」は0件だった。人間による同じメインチャット送信は既に複数回成功確認済みであり、ユーザー指示により重複試験は行わなかった。

source readyはファイル名の表示だけでなく、処理中表示なし、チャットのsource数が1以上、右側Studioの動画解説カードが有効、2秒安定を必要とする。5分timeout時は元Notebookのtitleへ`FAILED_`を付け、新規Notebook作成とsource uploadを1回だけ再試行する。2回目もtimeoutなら2件目も`FAILED_`化して停止する。Notebook本体とsourceは削除しない。

## GNB Creator comparison

一次資料`AutoGeminiNoteBookCreator`の基準HEAD`28bd51dfe2894018bfc9d65a02f219a933199127`では、source確認用メインチャット送信と、動画解説カードのカスタムトピックを使う動画生成が別工程だった。今回のユーザー実績と現行NotebookLM live DOMでは、動画生成指示をメインチャットへ送るだけで自動生成が開始することを確認したため、DJDmakerは現行成功flowへ合わせた。

## Chrome current-state gate

Start判定は過去のclose履歴ではなく、現在のAUTH Chrome管理processと専用profile lockをsource of truthにする。AUTH processが現在aliveならclose要求、deadまたは未起動ならprofile lockをbounded waitしてPre-flightへ進む。Google未認証は別の認証エラーとして表示し、「Chromeを閉じてください」と混同しない。

GUIのデザイン変更とPhase 2作業は行っていない。
