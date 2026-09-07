# 台本から授業動画つくるマシーン Ver1.2.5

台本TXTからNotebookLM動画を生成・回収し、音声末尾処理、固定Ending付与、HLS変換、ZIP化までをジョブ単位で実行するWindowsデスクトップアプリです。

GNBCreator / ドウガッチンガー / HLS Converter の3エンジン構成<br>
Created by 福ゼミ塾長

## Version

Version 1.2.5。Google認証は自動化flagのない通常Chromeで行い、そのChromeを閉じた後、同じ専用profileをautomation Chromeへ安全に引き継ぎます。授業動画作成開始時には、Notebookを作る前に7項目の自動Pre-flightを実行します。

PySide6 GUI、動画生成プリセット管理、永続Notebook scheduler、非同期Pipeline、ジョブ詳細・ログ・再実行、Ending preview、専用Chrome profile、動画artifact限定Web削除、Fake Notebook E2Eを含みます。NotebookLMのlive acceptanceでは、動画回収、12項目のRAW安全gate、artifact限定削除、refresh後の非復活まで確認しています。

このリポジトリではDBを使用しません。設定、キュー、状態、ジョブはアプリ配置フォルダ内のJSONへ原子的に保存する設計です。生成動画、ブラウザプロファイル、Cookie、ログ、秘密情報はGit管理対象外です。

## 開発環境

- Windows 10/11
- Python 3.11以上
- FFmpeg / ffprobe
- Google Chrome（NotebookLM操作で使用）

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e ".[dev]"
py -m pytest
```

GUI起動:

```powershell
.\.venv\Scripts\djd-maker.exe
```

Portable版は`DJDmaker_Ver1.2.5`を任意の書き込み可能な場所へ展開し、`DJDmaker.exe`を起動します。Portable版にはFFmpeg / ffprobeが同梱されています。Windowsの保護機能が警告した場合は、入手元と公開SHA-256を確認してください。

Ver1.2.4では1件のジョブ状態保存が既存の最大7回retryで復旧しない場合も、対象だけを「状態保存待ち」に隔離して他jobを続行します。Phase末尾で最大2回照合し、未解決は成功件数と分けて警告します。次回Startでは`system/recovery/deferred`のjournalとremote/local成果物を照合し、Preset・生成・予約・Downloadを盲目的に繰り返しません。journalも保存できなければmemoryに保持して警告します。詳細は[Ver1.2.4保存失敗隔離](docs/v124-jobstate-save-isolation.md)を参照してください。

Ver1.2.1ではStop/Window ×の協調停止と所有automation process限定の終了処理を追加しています。告知modalは既知の安全な情報dialogだけを閉じ、未知・確認dialogは自動で閉じず停止します。告知のlive自動dismissは自然再現せず未確認（ユーザー承認済みの既知事項）であり、fixtureで検証しています。

## Ver1.2 再開・移行

Ver1.2.5では、AI使用量上限・Chat無効の表示を検出すると、表示解除時刻に固定5分を加えた時刻まで生成処理を停止します。待機は `system/cloud-limit.json` に保存され、再起動しても維持します。期限後もNotebookの上限表示とChat有効状態を再確認してから再開します。時刻を取得できなければ自動再開しません。配布検証・SHA-256は[最終Release Gate](docs/v125-final-release.md)を参照してください。

上限中は新規Notebook、source投入、Preset/Chat送信、新規予約を行いません。既存のローカルRAW検証・Ending（任意）・HLS/ZIP・TXT移動・保存復旧を優先し、完成確認済み動画のDownloadも継続します。ローカル処理のためにremote削除を要求せず、未削除artifactは保持して削除待ち状態を残します。backlogが空ならNotebookを巡回せず待機します。

画面下部の `GUIタイプ` から白基調PHASE1とCyber HUD PHASE2を選択できます。選択は次回起動時も復元します。両画面は同じ操作ID・処理・状態表示を使用します。切替は表示接続を安全に再構築できる処理停止中のみで、一時停止中は切替できません。一時停止は次のNotebook移動・送信・Download・ローカル工程開始を止めます。開始済みの短い操作やFFmpegは安全checkpointまで進め、`再開`で同じ実行位置から続けます。詳細は[上限待機・Pause・Dual GUI検証記録](docs/v125-limit-pause-dualgui.md)を参照してください。

通常時はVer1.2.3以降の生成投入優先を維持します。Phase Aで未生成jobを1件ずつ確認→生成開始→保存→次jobへ進め、全対象の投入判定後にPhase BでDownload・RAW検証・Ending（任意）・HLS/ZIP・TXT移動を行います。上限検出時だけローカル処理へ切り替えます。生成待機は保存した次回確認時刻までNotebookを開かず、Phase Bで完成を検出したjobはその場で回収します。全jobの確認だけを先に行う事前巡回はしません。

工程ごとにJSON保存成功後、状態表の該当行・集計・Runtime表示を即時更新します。Stopや全件終了まで古い表示を残さず、ソート・チェック選択・スクロール位置を保持します。白GUIには現在Phase・Job・Notebook・工程・判断・次処理・結果・Attempt・経過時間・処理件数と日本語メッセージを表示します。検証記録は[Ver1.2.3](docs/v123-generation-first-live-status.md)を参照してください。

既知事項: `ANNOUNCEMENT_MODAL_LIVE_AUTO_DISMISS: UNVERIFIED_LIVE`（fixture PASS）、`SOURCE_UPLOAD_ERROR_LIVE_RECOVERY: UNVERIFIED_LIVE`。自然再現していないため実機修正成功とは扱いません。詳細は[Ver1.2.2 release記録](docs/v122-final-release.md)を参照してください。

Ending動画は任意です。設定を空にするとEnding結合をスキップし、検証済みRAWからHLS/ZIPを作成します。RAWそのものは変更しません。選択済みのEndingファイルが見つからない場合は、設定画面で再選択するかパスを空にしてください。

- 生成前のFAILEDは［授業動画作成開始］で既存Notebookとsourceを診断し、同じjob ID・保存済みpreset本文で再開します。過去のquota返信を現在の不足判定に流用しません。
- ［未回収動画のチェックから続ける］は生成済み／予約済み／未回収専用で、新規Notebookやpreset送信を行いません。
- COMPLETEDは再生成しません。完成時・起動時・Start時に残っている対応TXTをRAW保存先へ補完移動します。同名異内容を上書きせず、移動失敗でもCOMPLETEDを維持します。
- checkboxで完成jobを選択削除、または完成jobを一括削除できます。成果物・Notebook・sourceは削除しません。列見出しで全件を自然順に並べ替えできます。
- 旧版へ上書きせず、[Ver1.1→Ver1.2移行手順](docs/v12-final-release-gate.md)を確認してください。旧runtimeの自動importは行いません。
- 既知事項：`SOURCE_UPLOAD_ERROR_LIVE_RECOVERY: UNVERIFIED_LIVE`。upload完了の経緯は不明で、live retry成功とは扱っていません。安全性fixtureを確認済みで、自然再発時に追加検証します。

初回は「動画生成プリセット」からプリセット名と本文を登録してください。Ending動画の指定は任意です。プリセット一覧は保存されますが、選択状態は起動をまたいで保持しません。起動ごとに使用するプリセットを選択してください。未選択のまま開始した場合はNotebook作成前にエラー停止し、default・test・前回選択presetの自動投入はありません。選択本文はjob開始時にsnapshotされ、source ready後にメインチャットへ入力します。入力直後のDOM readback完全一致、送信後のuser message安定表示、Notebook側の動画artifact生成開始を確認します。通常pipelineは動画解説カードやGenerateボタンを直接操作しません。

Googleログインは次の正式フローです。

1. `Googleログイン`を押す。
2. 開いた通常ChromeでGoogleへログインする。
3. Gemini Notebookが表示されたら、そのChromeを閉じる。
4. `授業動画作成開始`を押す。

Start後のPre-flightと動画完成までの処理は自動で、正常時に追加操作はありません。アプリはpassword、Cookie、tokenを取得せず、CAPTCHA、2FA、本人確認を自動回避しません。

## 基本操作と自動処理

1. `input`へ台本TXTを配置し、設定画面で`raw_files`、`output`、Ending動画、動画生成プリセットを指定します。プリセット一覧は保存されますが、起動ごとに使用するプリセットを選択します。
2. 必要な場合は`Googleログイン`を押し、通常Chromeでログイン後、そのChromeを閉じます。CookieやpasswordをアプリやGitへ保存しません。
3. `授業動画作成開始`を押すと7項目の内部Pre-flight後、Notebook作成、rename、source投入、source ready監視、メインチャットへのpreset送信、Notebook側の自動動画生成、scheduler監視、Downloadをjob単位で実行します。sourceが5分以内にreadyにならない場合はNotebook名へ`FAILED_`を付け、新しいNotebookで1回だけ再試行します。
4. DownloadしたMP4を検証して`raw_files`へ永久保存した後、動画artifactだけをWeb UIから削除します。Notebook本体とsourceは削除しません。
5. RAWにEnding処理を行い、6秒segmentのHLSと無圧縮ZIPを`output`へ作成します。

一時停止は新しい工程の開始を止め、保存済みdeadlineとjob状態を維持します。再開・アプリ再起動後も同一NotebookやDownloadを重複作成しない設計です。RAWは後工程で上書き・削除されません。

## ディレクトリ

```text
input/       台本TXT
raw_files/   回収済み未編集MP4（上書き・自動削除禁止）
output/      完成ZIP
work/        ジョブ別の一時成果物
system/      settings.json / presets.json / queue.json / state.json / jobs/*.json
logs/        実行ログ
browser/     専用ブラウザプロファイル
src/         アプリケーションコード
tests/       自動テスト
docs/        調査・設計資料
```

空ディレクトリのみ `.gitkeep` で保持し、実データは除外します。

## 旧クレジット予約との互換性

- 生成開始前と生成要求の状態進行時にNotebookLMの明示的なクレジット表示を確認します。
- クレジット枯渇時に即時生成を繰り返しません。現在は新規予約を行わず、上限解除時刻+5分まで待機します。旧予約コード・selector・testは後方互換のため保持していますが、production生成経路からは呼びません。
- 旧予約済みjobは破壊せず、既存remote artifactの回収対象として保持します。リセット時刻は当日または翌日のtimezone付き日時として扱います。
- 予約・未回収状態は`system/jobs/*.json`に保持されます。［未回収動画のチェックから続ける］は既存Notebook/artifactだけを確認し、新規Notebookや重複動画を生成しません。リセット前は何も操作しません。
- ジョブJSON保存はjob/path単位のprocess内mutex、同一directoryの一時ファイル、flush/fsync、backup、atomic replaceを使用します。Windowsの一時的なWinError 5/32はbounded retryで内部復旧し、復旧できた場合はダイアログを表示しません。

詳細: [Ver1.1 credit/recovery/storage実装記録](docs/v11-credit-recovery-storage.md)

## Unit 0資料

- [開発ルール](docs/DEVELOPMENT_RULES.md)
- [3エンジン調査](docs/engine-audit.md)
- [統合アーキテクチャ](docs/architecture.md)
- [GUI設計](docs/gui-design.md)
- [テスト戦略](docs/test-strategy.md)
- [次Unitの推奨分割](docs/unit1-plan.md)
- [Unit 1実装記録](docs/unit1-implementation.md)
- [Unit 2実装記録](docs/unit2-implementation.md)
- [Unit 2エンジン忠実性補正](docs/unit2-engine-fidelity-patch.md)
- [Unit 3 Live Acceptance記録](docs/unit3-acceptance.md)
- [Unit 4 Multi-job Acceptance記録](docs/unit4-acceptance.md)
- [Unit 4 Portable検証](docs/unit4-packaging-verification.md)
- [Windows packaging preflight](docs/windows-packaging.md)
- [v0.1.2認証Retrospective](docs/v012-auth-retrospective.md)
- [GNBプリセット復元記録](docs/v013-preset-restoration.md)
- [Ver1.0機能修正記録](docs/v10-critical-function-fix.md)
- [Ver1.1 credit/recovery/storage実装記録](docs/v11-credit-recovery-storage.md)
- [Release artifact記録](docs/release-artifact-history.md)

## Buildとライセンス

Windows portable buildは[Windows packaging手順](docs/windows-packaging.md)を参照してください。Buildには内容を確認したFFmpeg、ffprobe、および対応する`FFmpeg-LICENSE.txt`を明示指定します。配布folderにはPython/PySide6/Playwright等のruntimeと、各componentのライセンス情報が含まれます。FFmpegのライセンス条件は採用buildとcodec構成に依存するため、再build時に必ず再確認してください。

## 安全原則

- RAW動画を後工程で上書きまたは自動削除しない。
- ダウンロードとRAWコピーの全検証が成功するまでNotebookLM側の動画を削除しない。
- `.crdownload`、一時ダウンロード、0 byte、ffprobe不合格ファイルを正常扱いしない。
- 旧仕様の「末尾を固定4秒削除」は使用しない。最終音声位置 + 0.5秒を終了点とする。
- CAPTCHA、本人確認、再ログイン、利用制限を迂回しない。
- 元3リポジトリを直接変更しない。
