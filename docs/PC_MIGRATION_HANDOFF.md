# DJDmaker PC移行引継ぎ

正式sourceはGitHub `https://github.com/G-fontis/DJDmaker.git` の`main`を正本とする。
local-onlyデータはDropboxの次の論理pathへ保存する。

`DELLノートPC(2019～)\VSCode_local\DJDmaker`

Ver2.3 handoffの基準は`main`の
`90d3f9c85368191bb7542b90aaa00a8dc75156f1`（914 tests PASS）である。次PCでは
このcommit以降を取得してからlocal-only dataを復元する。

次PCではDropbox rootやRepository rootが変わる前提とし、固定のユーザー名や
`C:\xampp\htdocs\PHP\DJDmaker`へ依存しない。

## 復元手順

1. GitHubからRepositoryをcloneし、`main`を最新にする。
2. Dropbox内の`VSCode_local\DJDmaker\MANIFEST.json`と
   `README_RESUME.md`を確認する。
3. Repository rootから、Dropbox側の実pathを指定してdry-runする。

   `python tools/restore_pc_migration.py "<Dropbox root>\DELLノートPC(2019～)\VSCode_local\DJDmaker"`

4. `CONFLICT`がないことを確認後、同じcommandへ`--apply`を加える。
5. settings内の台本・RAW・成果物・Ending pathはPC固有なので再設定する。
6. Ver2.3 handoffではユーザーの短期移行向け明示許可により、DJDmaker専用browser
   profileを含める。Chromeを完全終了してから復元し、DPAPI等のPC固有暗号化で認証を
   再利用できない場合は「Googleログイン」から通常AUTH Chromeで再ログインする。
7. `python -m pytest -q`と必要なportable smokeを実行してから開発を再開する。

helperは既定dry-runで、package内fileのsize/SHA-256を検証する。既存destinationと
内容が異なる場合は`CONFLICT`で停止し、自動上書き・自動削除をしない。

## 保存範囲

- 復元対象: settings、preset、production job JSON/runtime state、DJDmaker専用Chrome profile。
- 参照のみ: 既にDropbox等から次PCでも参照可能なRAW/MP4/HLS/ZIP/台本/Ending。
- 除外: 他project・個人Chrome profile、無関係なsecret/SSH、logs、cache、build/dist、
  再生成可能なtest/Acceptance成果物。

移行packageの正確なfile一覧・元path・復元先・hash・除外理由は
Ver2.3以降は`MANIFEST.json`を正本とする。秘密値はmanifestへ記載せず、
`contains_secret=true`だけを記録する。restore helperは既定dry-run、hash検証、競合時の
自動上書き禁止を維持する。
