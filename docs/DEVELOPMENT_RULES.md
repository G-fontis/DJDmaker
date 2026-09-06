# 開発ルール

## 作業終了通知音

- 開発作業を中断するとき、および依頼された作業を完了するときは、次の音源を再生する。

  `C:\Users\Ichiro\Music\Free音源\finish.mp3`

- 再生時にGUI、メディアプレイヤー画面、コンソール画面を表示しない。
- Windowsでは画面を持たない音声再生API、または非表示・音声専用モードのprocessを使用する。
- 音源の不存在や再生環境の問題で再生できない場合は、成果物やジョブ状態を変更せず、完了報告に再生失敗を明記する。
## 音源再生の並行タスク制御

- Codexがコマンド、検証、実装、監視などの処理を継続している間は、完了音・中断音を再生しない。
- 親タスクが未完了でも、ログイン、確認、選択などユーザーの応答が必要となりCodex側の処理が停止した時点では、中断通知として指定音源を必ず1回再生する。
- サブタスク単位の完了では音を再生しない。全タスクが終了したことを確認してから、全体の中断時または完了時に限り指定音源を1回再生する。
- 並行処理時は、全agentの状態を確認してから再生可否を判断する。

## 認証・ブラウザ変更のRelease Gate

- 認証またはブラウザ起動方式を変更するreleaseでは、既存Cookie/sessionをコピーしないFresh Profile Sign-in Acceptanceを必須とする。
- Warm profileの成功だけをFresh認証の証拠にしてはならない。Fresh、Warm、期限切れsessionを分けて記録する。
- 認証用Chromeではremote debugging、CDP、Playwright、headless、automation目的のflagを使用せず、password、Cookie、token、Google login DOMを取得・操作しない。
- Start時のPre-flightは完全な内部処理とし、正常時のユーザー操作を増やさない。正常フローはGoogleログインと授業動画作成開始の2操作だけとする。
- Pre-flight全項目がPASSするまでNotebook作成、source投入、動画生成を開始しない。
- 元GNB Creatorの動画生成presetは、選択本文をjobへスナップショットし、生成開始前にNotebookLMの動画解説カスタムトピック欄へ入力する。管理UIだけを実装して生成経路へ接続しない状態をPASSにしない。
- preset JSONはsettingsと同じprocess内mutex、temporary、flush/fsync、atomic replace、backup/recoveryを使い、保存用lock fileへ依存させない。

## EXE Build Cleanup

- EXE化アプリのversion更新時は、旧portable、旧build中間物、旧配布ZIPなど再生成可能な大容量成果物を、安全確認後に削除する。
- 旧version binaryを`dist`、`build`、検証用一時directoryへ無制限に残さない。
- 削除前に、対応source commit、version、必要なSHA-256、再build手順、ユーザーデータでないことを確認する。
- source、Git履歴、release文書、checksum記録、build script、spec、config template、test fixtureは保持する。
- `raw_files`、`output`、ユーザー設定、利用中browser profileなどユーザーデータをBuild Cleanupで削除しない。
