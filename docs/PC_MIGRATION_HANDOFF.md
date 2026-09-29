# DJDmaker PC移行引継ぎ

正式sourceはGitHub `https://github.com/G-fontis/DJDmaker.git` の`main`を正本とする。
local-onlyデータはDropboxの次の論理pathへ保存する。

`DELLノートPC(2019～)\VSCode_local\DJDmaker`

次PCではDropbox rootやRepository rootが変わる前提とし、固定のユーザー名や
`C:\xampp\htdocs\PHP\DJDmaker`へ依存しない。

## 復元手順

1. GitHubからRepositoryをcloneし、`main`を最新にする。
2. Dropbox内の`VSCode_local\DJDmaker\manifest.json`と
   `README_PC_MIGRATION.txt`を確認する。
3. Repository rootから、Dropbox側の実pathを指定してdry-runする。

   `python tools/restore_pc_migration.py "<Dropbox root>\DELLノートPC(2019～)\VSCode_local\DJDmaker"`

4. `CONFLICT`がないことを確認後、同じcommandへ`--apply`を加える。
5. settings内の台本・RAW・成果物・Ending pathはPC固有なので再設定する。
6. Google認証は「Googleログイン」から通常Chromeで再実施する。browser profile、
   Cookies、Login Data、Web Data、token、credentialは移行しない。
7. `python -m pytest -q`と必要なportable smokeを実行してから開発を再開する。

helperは既定dry-runで、package内fileのsize/SHA-256を検証する。既存destinationと
内容が異なる場合は`CONFLICT`で停止し、自動上書き・自動削除をしない。

## 保存範囲

- 復元対象: 秘密情報を含まないsettings、preset、存在するproduction job JSON。
- 参照のみ: 既にDropbox等から次PCでも参照可能なRAW/MP4/HLS/ZIP/台本/Ending。
- 除外: 認証済みChrome profile、Cookie DB、session/token/credential、logs、cache、
  build/dist、再生成可能なtest/Acceptance成果物。

移行packageの正確なfile一覧・元path・復元先・hash・除外理由は
`manifest.json`を正本とする。
