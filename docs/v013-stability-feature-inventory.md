# v0.1.3 実運用機能棚卸し

指示ID: `DJD-CHAPPY-V013-STABILITY-FIRST-REAL-USE-ACCEPTANCE-001`  
基準日: 2026-09-06

分類は A=元エンジン機能をadapterとして移植、B=DJDmakerの統合フローで同等以上を実現、C=意図的に不採用、D=必須だが未移植、とする。CはDJDmakerの確定安全仕様または統合後の操作モデルと競合するものだけに限定した。

| ID | Engine | 実運用機能 | 分類 | DJDmakerの所在、または不採用理由 |
|---|---|---|---|---|
| GNB-01 | GNBCreator | 専用Chrome profileとGoogle認証session | B | `BrowserManager`の通常Chrome AUTHから同一profileのautomation Chromeへhandoffする。 |
| GNB-02 | GNBCreator | Notebook作成、rename、TXT source upload、source ready待機 | A | `NotebookDomAdapter` / `NotebookEngineAdapter`。titleとsourceはDOM readbackする。 |
| GNB-03 | GNBCreator | 生成文章presetのCRUD、選択、再起動復元 | A | `PresetRepository`と`SettingsDialog`。選択IDも明示保存する。 |
| GNB-04 | GNBCreator | 選択preset本文を動画カスタムtopicへ送信 | A | job開始時に`generation_prompt`へsnapshotし、`VIDEO_CUSTOM_TOPIC`へfill後に生成する。 |
| GNB-05 | GNBCreator | 生成完了監視と生成中・予約・失敗の区別 | A | playable video artifactを完成根拠とし、永続scheduler deadlineでpollする。 |
| GNB-06 | GNBCreator | 対象artifactの一意なdownload | A | jobのNotebook identityと対象video cardを照合し、job専用download directoryへ保存する。 |
| GNB-07 | GNBCreator | download安全検証とRAW保存 | B | 12項目gate、ffprobe、stream、duration、size安定、RAW再検証を統合した。 |
| GNB-08 | GNBCreator | 動画artifactだけをWeb UIで削除 | A | 12項目gate後だけartifact menuの削除を実行し、refresh後の非存在まで確認する。 |
| GNB-09 | GNBCreator | restart/recovery/retry | B | job JSON checkpoint、Notebook ID/URL、scheduler deadline、job詳細retryで自動復旧する。 |
| GNB-10 | GNBCreator | pause/stop、job状態・log確認 | B | Main GUI、`GuiPipelineController`、job詳細、相関logへ統合した。 |
| GNB-11 | GNBCreator | 実行時だけpreset本文を上書きする別入力欄 | C | 選択presetをjob開始時に固定する確定仕様と競合する。編集はpreset編集で行い、新規jobだけへ反映する。 |
| GNB-12 | GNBCreator | 1 batch内の生成本数指定 | C | DJDmakerはinput TXT 1件=job 1件で、待機TXT全件を処理する。重複生成防止とjob identityを優先する。 |
| GNB-13 | GNBCreator | Notebook全体削除、source削除 | C | 確定安全仕様で禁止。削除対象は検証済み動画artifactだけである。 |
| GNB-14 | GNBCreator | chat用stop wordsと旧generation timeout UI | C | 動画artifact DOM状態と永続deadlineに置換済み。旧chat文言判定は動画監視へ適用しない。 |
| DVG-01 | ドウガッチンガー | 最後の有効音声位置を末尾から探索 | A | `EndingEngineAdapter`が既存方針のsilence解析と末尾逆探索を使用する。 |
| DVG-02 | ドウガッチンガー | 最終音声位置+0.5秒でcut | A | `audio_tail_padding_seconds=0.5`を安全baselineとして固定する。 |
| DVG-03 | ドウガッチンガー | 固定Ending 1本のconcat | A | job別stagingへ出力し、RAWを上書きせずEndingを結合する。 |
| DVG-04 | ドウガッチンガー | ffmpeg/ffprobe出力検証 | A | stream、duration、codec、出力存在をpublish前に検証する。 |
| DVG-05 | ドウガッチンガー | multi-job、cancel、retry identity | B | UUID job、job別work directory、bounded media lane、checkpoint retryへ統合した。 |
| DVG-06 | ドウガッチンガー | Eyecatch/random/groupなど別製品固有合成 | C | 授業動画の確定フローはRAW+固定Ending 1本。別製品固有の編集モードは対象外である。 |
| HLS-01 | HLS Converter | H.264/AAC HLS変換 | A | `HlsAdapter`がffmpeg/ffprobe探索と変換commandを踏襲する。 |
| HLS-02 | HLS Converter | 6秒segment、VOD playlist、連番TS | A | `-hls_time 6`、`playlist.m3u8`、連番segmentを固定する。 |
| HLS-03 | HLS Converter | playlist/segment完全性検証 | A | path、存在、非0 byte、連番、ENDLIST、decodeを検証する。 |
| HLS-04 | HLS Converter | ZIP_STOREDとZIP integrity | A | playlistとTSだけをroot直下へ格納し、CRC、entry集合、非0 byteを再検証する。 |
| HLS-05 | HLS Converter | queue継続とjob failure隔離 | B | media laneとjob単位FAILEDに統合し、他jobを継続する。 |
| HLS-06 | HLS Converter | 変換後のWindows shutdown | C | DJDmakerのGUI常駐・複数job・restart/resumeモデルと競合し、予期しないPC終了を避ける。 |

集計: A=14、B=6、C=6、D=0。実運用に必須の未移植機能は0件。Cの6件は機能を発見できなかった項目ではなく、確定仕様または統合製品の安全モデルにより意図的に不採用とした項目である。
