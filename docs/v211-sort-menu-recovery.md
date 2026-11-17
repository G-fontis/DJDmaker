# Ver2.11 Sort menu recovery

Notebook Homeの並び替えメニューを閉じて処理を再開します。従来は`SCRIMS`に含まれるCDK backdropの存在だけでUNKNOWN MODALと判定し、タイトルのクリックを遮る既知メニューまでHuman Waitへ移行していました。

## 判定と復帰

`adapters/sort_menu.py`はHTTPSの`notebook.google.com` Homeで、唯一の`Sort notebooks`ボタン、表示中のrole=menu、正確に2件のmenuitemradio（Most recent / Title）を確認します。aria-controlsがあればmenu idと照合します。backdropがある場合は、同じCDK containerの直下で、transparent backdropの次のhostが対象menuを含むことを確認します。別dialog、追加backdrop、不明なblocking overlayがある場合は既存の保護へ戻します。

元のmenu/backdropをElementHandleで保持し、各操作前に再判定して、対象menuへEscapeを最大2回送ります。menuとbackdropの消失、Home identity、Notebookタイトルのvisible/enabledとPlaywright trial clickを確認して復帰します。並び順の選択項目や他のmodalの×は押しません。Download中はEscapeを送信しません。

解除できない場合は専用の人手確認表示へ移り、FAILEDやfatalにせず、人が閉じた後に再開します。Human Waitの入口と待機中にも同じ判定を使い、古い誤判定の待機を解除します。runtimeイベントはJob設定を書き換えず、既存schedulerの安全な境界で生成優先を再評価します。GENERATING/READY/WAITINGの既存checkpointからの再開でSource・Preset・生成の重複がないことをテストしました。

Chromeの復元通知はページDOMとは別のブラウザーUIです。通知の復元・×・Chrome再起動を行うコードは追加していません。実機では通知が表示されず、無害な通知を模したfixtureで回帰検証しました。ネイティブ通知を強制再現しての確認は未実施です。

## 検証

- 既存1190件に29件を追加。全1219件のsource/release testsを実行。
- 実Notebookで旧判定のHuman Waitを再現し、新判定でmenu/backdrop解除、タイトルclick、期待するNotebook identity、read-only状態照会READYを確認。
- 実Notebookの並び順変更・生成・Source送信・Notebook/artifact削除は0。BrowserManager所有の専用automation Chromeのみを使用し、手動AUTH Chromeの起動方式・Current Window方式は変更していません。
- source smokeとEXE smokeで両GUI、最大2回失敗後の人手復帰、未知modal保護、Job JSON不変を確認。Credit Limit、Download、shutdown等を含む16 EXE suitesとPortable検証を実行。
- 証跡は開発端末の`work/v211/`に保存。配布物には実profile、Job、ログ、fixture HTML/mediaを含めません。

## 配布と旧成果物

正式Versionは2.11.0、Windows file versionは2.11.0.0、GUI表記はVer2.11です。`dist/DJDmaker_Ver2.11`、ZIPとSHA256SUMSを生成し、ZIP展開後にもPortable検証を実施します。

旧公開Ver2.10 ZIPのみ、CRC・収録内容・SHA-256を検証して削除対象にします。旧公開folderはcleanup時点で存在しませんでした。Dropboxの既存配備・認証profile・ユーザーデータは保持します。旧sourceは`e053c9f8af39eb922dbeccf3e8e54badf737905a`、旧ZIP SHA-256は`e3aad742ef01ee3bb6d48cdd6b45ce43ee8c61735312260d73d3d2e25531f4f8`、旧EXEは`c23e86218ce9ac2ab27dc307f4f63db5c0c15521b75e79ed0976fdd8caf179fa`です。再buildには当該source、`packaging/build_windows.ps1`、保持したspec/config/build assetsおよび検証済みFFmpeg/ffprobeを使用します。

最終検証結果は1219 passed（追加29件、失敗・skip 0）、EXE 16/16 PASS、Portableと最終ZIP展開後の検証PASSです。ZIPは278,154,223 bytes、SHA-256は`42e99022058984e661855ca0cb8393ff77b578315b9558e0307051ecde1fdc62`、EXE SHA-256は`e60e0e14045ab7e79a188f37aad51b204d1455c520962d5e7dadf5464eddd7ad`です。Package auditは409 files、private/user runtime/fixture dataは0。本番保護対象2575ファイルの変更は0です。
