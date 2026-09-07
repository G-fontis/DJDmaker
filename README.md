# 台本から授業動画つくるマシーン Ver1.2.3

この`phase2`ブランチはVer1.2.3正式Backendと承認済み静的HUDの統合版です。mainへは反映していません。配布フォルダは`DJDmaker_Ver1.2.3_Phase2`です。左にAction Console、中央にJob一覧・処理ステップ・実行ログ、右に状態・現在タスク・認証・Credit・Storageを配置しています。

台本TXTからNotebookLM動画を生成・回収し、音声末尾処理、固定Ending付与、HLS変換、ZIP化までをジョブ単位で実行するWindowsデスクトップアプリです。

GNBCreator / ドウガッチンガー / HLS Converter の3エンジン構成<br>
Created by 福ゼミ塾長

## Version

Version 1.2.3。Google認証は自動化flagのない通常Chromeで行い、そのChromeを閉じた後、同じ専用profileをautomation Chromeへ安全に引き継ぎます。授業動画作成開始時には、Notebookを作る前に7項目の自動Pre-flightを実行します。

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

Portable版は`DJDmaker_Ver1.2.3_Phase2`を任意の書き込み可能な場所へ展開し、`DJDmaker.exe`を起動します。Portable版にはFFmpeg / ffprobeが同梱されています。Windowsの保護機能が警告した場合は、入手元と公開SHA-256を確認してください。

Ver1.2.1ではStop/Window ×の協調停止と所有automation process限定の終了処理を追加しています。告知modalは既知の安全な情報dialogだけを閉じ、未知・確認dialogは自動で閉じず停止します。告知のlive自動dismissは自然再現せず未確認（ユーザー承認済みの既知事項）であり、fixtureで検証しています。

## Ver1.2 再開・移行

Ver1.2.3は生成投入を優先します。Phase Aで未生成jobを1件ずつ確認→生成開始または予約→保存→次jobへ進め、全対象の投入完了後にPhase BでDownload・RAW検証・Ending（任意）・HLS/ZIP・TXT移動を行います。Phase AではDownload・ローカル変換を開始しません。生成待機は保存した次回確認時刻までNotebookを開かず、Phase Bで完成を検出したjobはその場で回収します。全jobの確認だけを先に行う事前巡回はしません。

工程ごとにJSON保存成功後、状態表の該当行・集計・Runtime表示を即時更新します。Stopや全件終了まで古い表示を残さず、ソート・チェック選択・スクロール位置を保持します。Phase2 HUDには現在Phase・Job・Notebook・工程・判断・次処理・結果・Attempt・経過時間・処理件数と日本語メッセージを表示します。検証記録は[Ver1.2.3](docs/v123-generation-first-live-status.md)と[Phase2統合](docs/phase2-v123-integration.md)を参照してください。

既知事項: `ANNOUNCEMENT_MODAL_LIVE_AUTO_DISMISS: UNVERIFIED_LIVE`（fixture PASS）、`SOURCE_UPLOAD_ERROR_LIVE_RECOVERY: UNVERIFIED_LIVE`。自然再現していないため実機修正成功とは扱いません。詳細は[Ver1.2.2 release記録](docs/v122-final-release.md)を参照してください。

Ending動画は任意です。設定を空にするとEnding結合をスキップし、検証済みRAWからHLS/ZIPを作成します。RAWそのものは変更しません。選択済みのEndingファイルが見つからない場合は、設定画面で再選択するかパスを空にしてください。

- 生成前のFAILEDは［授業動画作成開始］で既存Notebookとsourceを診断し、同じjob ID・保存済みpreset本文で再開します。過去のquota返信を現在の不足判定に流用しません。
- ［未回収動画のチェックから続ける］は生成済み／予約済み／未回収専用で、新規Notebookやpreset送信を行いません。
- COMPLETEDは再生成しません。完成時・起動時・Start時に残っている対応TXTをRAW保存先へ補完移動します。同名異内容を上書きせず、移動失敗でもCOMPLETEDを維持します。
- checkboxで完成jobを選択削除、または完成jobを一括削除できます。成果物・Notebook・sourceは削除しません。列見出しで全件を自然順に並べ替えできます。
- 旧版へ上書きせず、[Ver1.1→Ver1.2移行手順](docs/v12-final-release-gate.md)を確認してください。旧runtimeの自動importは行いません。
- 既知事項：`SOURCE_UPLOAD_ERROR_LIVE_RECOVERY: UNVERIFIED_LIVE`。upload完了の経緯は不明で、live retry成功とは扱っていません。安全性fixtureを確認済みで、自然再発時に追加検証します。

初回は設定画面でEnding動画を指定し、「動画生成プリセット」からプリセット名と本文を登録してください。プリセット一覧は保存されますが、選択状態は起動をまたいで保持しません。起動ごとに使用するプリセットを選択してください。未選択のまま開始した場合はNotebook作成前にエラー停止し、default・test・前回選択presetの自動投入はありません。選択本文はjob開始時にsnapshotされ、source ready後にメインチャットへ入力します。入力直後のDOM readback完全一致、送信後のuser message安定表示、Notebook側の動画artifact生成開始を確認します。通常pipelineは動画解説カードやGenerateボタンを直接操作しません。

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

## Ver1.1 クレジット枯渇と未回収動画

- 生成開始前と生成要求の状態進行時にNotebookLMの明示的なクレジット表示を確認します。
- クレジット枯渇時は即時生成を繰り返さず、NotebookLMの完全一致した予約操作だけを使用します。予約成功はリモートの予約待機状態を確認して確定します。
- リセット時刻はNotebookLMが表示するローカル時刻から、当日または翌日のtimezone付き日時として保存します。残量表示を取得できない場合も、枯渇の明示表示があれば安全な予約処理を継続します。
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
