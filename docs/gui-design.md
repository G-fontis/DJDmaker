# Unit 0: GUI構造案

ウィンドウタイトルは「台本から授業動画つくるマシーン v0.1」。headerまたはAboutに次を常時確認可能にする。

> GNBCreator / ドウガッチンガー / HLS Converter の3エンジン構成<br>
> Created by 福ゼミ塾長

## Main window

- 設定summary: 台本folder、RAW保存先、ZIP出力先、Ending動画。各pathの選択/開く、Ending再生確認。
- 操作bar: 台本再読込、授業作成開始、一時停止、停止、ログを見る、設定。
- status summary: 現在job、現在工程、進捗率、Notebook完成/総数、ZIP完成/総数、error数、次回監視までの残り時間。
- job table: `No / 台本名 / Notebook / End処理 / HLS/ZIP / 状態`。工程表示は `－ / ▶ / ○ / ×` とtextを併記し、色だけに依存しない。

## Settings dialog

- folder 3種とEnding path（前回値をJSON保存）
- 初回確認600秒、以後poll 120秒、末尾余白0.5秒
- FFmpeg同時数1または2（初期値1）
- ffmpeg/ffprobe/Chrome検出結果
- 保存前validateとdefaultへ戻す

## Job detail dialog

- job ID、TXT、Notebook ID/URL、各成果物path
- Notebook内部step、End内部step、HLS/ZIP内部stepのtimeline
- attempt履歴、error code/message、削除安全ゲート12項目
- 操作: job再実行、Geminiから再回収、Endから再実行、HLSから再実行、RAWを開く、ZIPを開く、Notebookを開く、error詳細
- remote削除は通常自動ゲート経由のみ。手動強制削除は設けない。

## Log window

時刻、job、engine、stage、level、messageでfilterする。Cookie/token/HTML input値はredactする。診断exportにもbrowser profileや認証情報を含めない。

UI threadではPlaywright/FFmpeg/JSON大量I/Oを行わない。workerからimmutable eventをsignalで送り、UIはview modelだけを更新する。閉じる操作時は実行中jobを表示し、安全な停止かbackground継続不可を明示する。
