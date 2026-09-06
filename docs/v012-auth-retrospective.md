# v0.1.2 Google認証 Retrospective

## 結論

v0.1.2はGoogleログイン用Chromeの起動時点からremote-debugging関連flagを付け、その同じprocessへCDP接続する設計だった。認証済みprofileではNotebook E2Eまで成功したが、Fresh profileでGoogle sign-in画面へ進むと「このブラウザまたはアプリは安全でない可能性があります」が再現した。

## 見逃した理由とAcceptance profile

- v0.1.2 live記録は同一PID handoffとE2Eを証明したが、Fresh profile作成、password入力開始、Google sign-in通過、security rejection 0を証明していなかった。
- 主検証profileは2026-09-06 04:48頃に作成済みで、live handoffより前からGoogle sessionを持っていた。
- portable検証profileの`Default/Cookies`はコピー先作成時刻より古い更新時刻を保持し、元profileの更新時刻と一致した。Cookie内容は読み取っていない。
- したがってportable AcceptanceはWarm/copied session検証であり、Fresh認証試験ではなかった。

## Fresh v0.1.2直接再現

2026-09-06、既存profile/sessionをコピーしない新規profileでv0.1.2の認証Chromeを起動した。Chromeはinstalled executableを使用し、`--user-data-dir`、`--remote-debugging-address=127.0.0.1`、`--remote-debugging-port=0`、`--remote-allow-origins=*`等を付けていた。Google sign-in時にユーザー報告どおりのsecurity rejectionを直接確認した。password、Cookie、tokenは記録していない。

## GNB Creatorとの差

GNB Creator HEAD `28bd51dfe2894018bfc9d65a02f219a933199127`は、認証時にinstalled Chromeを`--user-data-dir`と`--no-first-run`だけで通常起動し、終了後に同じprofileをPlaywright `launch_persistent_context`へ渡していた。認証Chromeにはremote debugging/CDP/Playwright/headless flagがない。

## v0.1.3設計変更

1. AUTH phaseは通常Chromeだけを起動し、人間がログインして閉じる。
2. Start時にAUTH Chrome終了とprofile lock解放を確認する。
3. 同じ専用profileで別のautomation ChromeをPlaywright persistent contextとして起動する。
4. Notebook作成前に接続、認証、home DOM、必要selectorを含む7項目を自動確認する。
5. 失敗時はNotebookを作らず、必要最小限の案内だけを表示する。

## 再発防止

- 認証/browser変更releaseではFresh Profile Sign-in Acceptanceを必須化する。
- Fresh、Warm、expiredを独立Gateにする。
- Pre-flightはside effect 0で、正常時の操作は`Googleログイン`と`授業動画作成開始`の2回だけとする。
