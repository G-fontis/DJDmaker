# Ver2.2 Download品質・Scheduler・保存リトライ作業記録

指示ID: `DJD-CHAPPY-V22-SCHEDULER-DOWNLOAD-QUALITY-MANUAL-RETRY-FULL-002`

この文書はVer2.2 release Gateの実施記録である。

## 実RAW統計

- directory: `C:\Users\Ichiro\Desktop\test_output\Raw_Files`
- MP4総数: 24、10 MiB以上候補: 19、採用: 19、ffprobe不正除外: 0
- mean: `0.084634408639 MiB/sec`
- sample standard deviation (n-1): `0.015480616842`
- lower 3σ: `0.038192558112 MiB/sec`
- sample min/max: `0.025949201110` / `0.098746173417 MiB/sec`
- duration: `370.474376`–`598.796190 sec`
- size: `14,680,064`–`58,868,323 bytes`
- 10 MiB未満5件もffprobeは成功したが、ratioは`0.002757912044`–`0.005896301347`で全件下限未満。

## Root causeと修正

旧Chrome CDP経路は非`.crdownload`の存在を完成候補にでき、page close時には1秒size stableの`.crdownload`さえfinal一時fileへrenameできた。このためbrowser completionより前の途中保存を採用し得た。V22 liveでも2,493,844-byte等の`.crdownload`が停止し、CDP progressがcompletedへ到達しない事実を再現した。新実装はPlaywrightのcompleted Download promiseを主経路、CDPを互換fallbackとし、`.crdownload`昇格を廃止する。さらに非temporary、1秒size stable、ffprobeをadapter gateとし、pipelineで統計gateを再実行する。

attempt候補はwork内だけに置く。完了時刻からの品質待機は1, 31, 61, 91, 121, 151秒。PASS時のみcanonical downloadへatomic replaceし、その後RAW安全gateへ進む。回数、候補、完了時刻、次判定時刻、size、duration、ratio、threshold、結果をjob JSONへ保存する。

手動保存リトライは両GUIの同じCommand/Application Serviceを通る。選択時の4つの出力設定をsnapshotし、READY確認後にDownloadだけを開始する。旧RAW・旧完成成果物は新一式の検証完了まで保持し、remote artifactは削除しない。

## Gate状況

- 実統計: PASS
- 新規source tests: PASS
- 全回帰: `892 passed`
- live manual保存リトライ: PASS（1回目、45,590,770 bytes、539.515646 sec、ratio `0.080588481051`）
- live Scheduler: due jobを優先3で選択し、待機せずDownloadへ遷移することを確認
- retry上限: 初回+5回の計6回、7回目なし、枯渇時は`DOWNLOAD_RETRY_EXHAUSTED`
- 禁止操作: Notebook作成・source upload・Preset送信・動画生成・artifact削除はいずれも0
- 本番job JSON変更: 0
- Version: Python/Product `2.2.0`、FileVersion `2.2.0.0`、GUI `Ver2.2`
- portable onedir build: PASS
- Fresh ZIP展開（日本語＋空白path）: GUI、settings再起動、browser、preset、Fake E2E、FFmpeg/ffprobe PASS
- package: browser profile、Cookie、実RAW、実HLS、実ZIP、log、job stateを含めず、空のruntime folderだけを格納
- live evidence: `work/v22-live-manual-005/result.json`
- portable evidence: `work/v22-portable-verification.json`、`work/v22-fresh-extracted-verification.json`
- commit / push: 全Gate確認後に通常pushする
