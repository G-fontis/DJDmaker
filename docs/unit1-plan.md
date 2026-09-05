# 次Unitの推奨分割

依存順を守り、独立作業は並行可能とする。

1. Unit 1A: JSON repository、schema version、job scan、atomic recovery、settings GUI。
2. Unit 1B: 共通ffprobe validator、immutable RAW writer、12項目remote deletion gate。
3. Unit 1C: GNBCreator adapter（DB層を除外）、fake DOM/Notebook engine tests。
4. Unit 1D: ドウガッチンガーのsingle-ending adapterと実FFmpeg fixture tests。
5. Unit 1E: HLS adapter、HLS/ZIP強化検証と実FFmpeg fixture tests。
6. Unit 1F: bounded pipeline coordinator、pause/stop/restart、failure isolation。
7. Unit 1G: PySide6 main/settings/detail/log画面と統合acceptance test。

最初のreview gateは1A/1B完了時。次に各engine adapterを個別reviewし、最後にcoordinatorへ接続する。NotebookLMの現在DOM、remote動画だけを削除できる操作、配布時のアプリdata配置方針は人間確認を必要とする。

