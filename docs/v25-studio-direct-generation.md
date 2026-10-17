# Ver2.5 Studio Direct

動画生成の通常経路は、Source READYと必要Sourceの選択確認後、Studio内の「動画解説」から動画設定を開き、説明動画・日本語・プリセットのスタイルを指定する方式です。設定画面内の「生成」を押す直前に設定値を読み直します。生成用のプリセット本文をChatへ送信せず、確認カードや「確認→続行」を必要としません。旧Chat方式は明示指定の互換経路としてのみ残します。

## 設定と保存

既存4件の本文は、Source読み込み完了・説明動画・日本語・スタイルの定型指示でした。ペーパークラフト／アニメ／ホワイトボード／カワイイを構造化設定に対応付けます。旧JSONは読込時に補完し、編集するまで書き換えません。本文は保持します。定型外の追加指示は黙って捨てず、対応確認が必要なエラーにします。

Jobは本文とSHA-256、video_type・language・visual_styleとそのSHA-256を独立して保存します。開始後のプリセット編集は既存Jobに影響しません。現行UIの「ペーパー クラフト」は内部値paper_craftに対応します。Producer Noteは任意で、今回の3設定だけの生成ではOFFです。

## 重複防止と回復

Generate直前に操作意図を永続化し、その後1回だけクリックします。保存に失敗した場合はクリックしません。クリック結果が不明な場合は再クリックせずremote状態の照合待ちにします。再起動時もremote GENERATING／READYを先に確認し、生成済みなら待機／回収へ進みます。未生成かつ未操作なら開いた設定を再確認して続行します。

失敗動画の再生成も同じStudio経路で、保存した設定を復元します。Studioの「再試行」は押さず、failed artifactを保持し、既存の最大3回制限を適用します。操作結果未確定の記録は、古いfailed artifactが残っていても解除しません。

Source READYで生成可能なJobはP1候補に残します。未着手である証拠を保存できたJobを、旧方式の送信結果不明と混同して保存待ちに隔離しません。実上限中は生成せず、上限接近の警告だけなら続行します。

## モーダルと既存機能

動画設定ダイアログは見出し・言語・形式・生成ボタンの組合せで、その操作中だけ識別します。未知モーダルや別のoverlayは従来どおり人間による確認待ちです。閉じた後は同じNotebookとremote状態を確認して再開します。広告製品名による特例はありません。

Current Window、Download popup／完了待ち／Guard／Deferred Queue、保存リトライ、remote保持、独立したEnding・末尾カット・Encode、HLS/ZIP設定を維持します。

## 検証

Ver2.4基準は952 tests PASS。Studioテストでは実Chromiumと個人情報を除いた実設定DOMを使い、設定選択・スコープ・誤操作防止・保存失敗・再起動・未知モーダル・Quota・再生成・P1を検証します。EXE用の隔離テストにも同じ設定DOMを同梱します。

2026-10-17の実Notebookでは、専用の検証NotebookにTXTを追加してSource READYを確認し、製品のStudioGenerationを実行しました。説明動画・日本語・ペーパークラフトを読み戻し、「生成」1回、生成用Chat送信0回、remote GENERATING、GENERATION_STARTEDの永続化を確認しました。既存Notebook・Source・artifactの削除はありません。自然なfailed generationは再現せず、再生成は隔離DOMとEXEで検証します。

証跡は配布物と分離したwork/v25-live/live-result.json、各pytest XML、EXE受け入れJSONに保存します。ブラウザprofileや生成動画を正式配布物へ含めません。

Version昇格前の全体テスト991件と最終Studio／GUIテスト42件がPASSした後、2.5.0へ昇格しました。昇格後の最終全体テストは994件PASS（既存952＋追加42）です。実NotebookのGenerateは1回、生成用Chat送信と重複生成は0回でした。

ビルド後は正式配布フォルダを変更せず、隔離コピーでStudio Direct、未知モーダル、Download完了、終了、Source／生成失敗回復、Quota／両GUI、Current Window、逐次処理、状態保存隔離、HLS任意化の10系統がすべてPASSしました。別コピーでポータブル起動・設定保持・同梱FFmpeg・browser・preset・Fake E2Eを確認し、PHASE2→再起動→PHASE1の設定保持もPASSしました。system/configの40ファイルは作業前とhash一致し、追加0です。
