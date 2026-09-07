# Ver1.2.5 最終Release Gate

返答ID: DJD-CODEX-20260908-V125-FINAL-003

今回指示ID: DJD-CHAPPY-V125-BUILD-EXECUTION-AUTHORIZED-001

対象: `C:\xampp\htdocs\PHP\DJDmaker` / `main`

この文書はcommit直前の製品Gate記録。commit自身のSHA・push結果・GitHub照合を含む最終60項目報告は `work/v125-final-release-result.txt` および最終返答へ保存する。過去のbuild拒否記録は履歴として保持し、現在の状況と区別する。

## Build再開と検証側の修正

ユーザーが環境設定を変更した後、LocalMachine=RemoteSigned（他scope Undefined）、実効RemoteSignedを確認。既存 `packaging/build_windows.ps1` がexit 0で完了し、ExecutionPolicy拒否は解消した。Codexからのpolicy変更・Bypass使用なし。

既存PyInstaller 6.22.2 onedir / windowed方式を維持。Python 3.14.6、PySide6 6.11.2。VersionはPython/Product 1.2.5、File 1.2.5.0、GUI Ver1.2.5。

初回EXEの保存隔離試験では、21回の保存失敗注入・他job完了・隔離job復旧・重複0は成功したが、旧 `job_changed` signalで描画前に比較する検証コードがFAILした。mainのGUIは共通 `presentation_event` をQueuedConnectionで描画するため、検証も同じEventのGUI consumerより後へ接続した。業務処理は変更せず、chat.send / generation.accepted / hls.start / COMPLETEDの一致条件も維持。再build後は全観測stageと表示の不一致0でPASS。

GUI再起動試験のWindows UIAutomation試行ではQt ComboBoxのSelectionPatternが未対応で、ValuePattern/keyboard試行も切替確認に至らなかった。製品の失敗と混同せず、opt-in EXE verifierから実ComboBoxの既存signal/Command経路を操作し、3つの独立EXE processで起動時選択と永続値を確認した。

EXE Limit試験も、配布物内の検出器を実ChromeのローカルDOM fixtureで動かすよう補強。Googleアクセスや意図的なクレジット消費は行わない。既存の実Notebook証跡とfixtureの区別を保持する。

## 正式候補成果物

- Portable: `dist/DJDmaker_Ver1.2.5/`
- EXE: `dist/DJDmaker_Ver1.2.5/DJDmaker.exe`、3,978,215 bytes
- ZIP: `dist/DJDmaker_Ver1.2.5.zip`、277,766,841 bytes
- checksum: `dist/DJDmaker_Ver1.2.5_SHA256SUMS.txt`
- EXE SHA-256: `A533F37409FCE458D2B5D1E9ECD4B31A4D16C13A36EC1A70D186DCC4773C63CF`
- ZIP SHA-256: `1F3DEEA6E741DE01D6038779FF0F19982CC23D86A3CEF4A002BDAF5E1907A970`

最初の候補EXE `02E67B1E1815F5F2DE1FE3AA371C6C1D7371AA1E84707C3A284A83E75F9ADECE` から変更。理由は上記のpackaging verifier更新と再build。Version/業務処理の追加変更はない。

ZIP CRC/integrity PASS。日本語＋空白path `work/v125 最終展開 日本語 space/DJDmaker_Ver1.2.5` へ新規展開し、全408配布ファイルのSHA一致を確認。その後、展開copyだけでGUI、settings別process write/read、Chrome smoke、preset、Fake E2E、同梱FFmpeg/ffprobe、HLS/ZIPを確認した。正式dist/ZIPのruntime領域は空であり、試験データを戻していない。

## 60項目報告

| # | 項目 | 結果 |
|---|---|---|
| 1 | HEAD before | 6dd77183ec8eca14412502c432c5e984e0b4bb5c |
| 2 | 現行Notebook UI | visible使用量上限/Chat無効/解除時刻を実DOM確認 |
| 3 | HTML limit DOM | view-sourceでは動的本文なし。実ページで補完 |
| 4 | detector | visible文言＋中央Chat disabled、source/会話除外 |
| 5 | parser | local timezone、HH:mm、午前午後、日跨ぎ、時刻不明時は待機 |
| 6 | +5分 | live 06:21→06:26、最終EXE fixture 07:51→07:56 |
| 7 | persistence | atomic system/cloud-limit.json、再起動保持tests PASS |
| 8 | cloud block | EXE試験で未生成100件、Preset/生成/予約等cloud calls 0 |
| 9 | local fallback | RAW全gate→Ending skip→HLS→ZIP→COMPLETED→TXT move PASS |
| 10 | backlog empty | 解除予定前は待機し、新規Notebook巡回0 |
| 11 | resume check | 既存liveで06:26後Chat enabled。未解除・再延長・確認失敗はtests PASS |
| 12 | production予約 | 新規予約呼出0 |
| 13 | reservation source | legacy実装/helper/selector/test保持 |
| 14 | legacy state | 旧予約jobを保持し、既存回収互換tests PASS |
| 15 | Pause根因 | scheduler flagだけではcycle内部が止まらなかった |
| 16 | Pause修正 | 共通Condition/checkpoint、観測timeoutからPause時間除外 |
| 17 | Pause navigation | 既存source live増分0、EXE上限待機中cloud calls 0 |
| 18 | Pause send | 0 |
| 19 | Resume重複 | source100件fixture PASS、EXE local結果/呼出数で重複なし |
| 20 | PHASE1 | 最終EXEで白GUI表示PASS |
| 21 | PHASE2参照元 | f6f5871925f44718f51a106f7f55fa44e8f882b1 |
| 22 | 統合方法 | presentationのみ。main backendを維持 |
| 23 | 最新機能 | runtime/save deferred/checkbox/delete/sort/optional Ending等を共通化 |
| 24 | switch | 停止中切替。最終EXEで両mode往復PASS |
| 25 | 選択保持 | 3つのEXE processでPHASE1→PHASE2保存、PHASE2起動→PHASE1保存、PHASE1起動PASS |
| 26 | Command IDs | 共通19 IDs。全一覧は実装報告/core/commands.py参照 |
| 27 | registry | unique/required検証PASS |
| 28 | Dispatcher | 共通CommandRouter/Application Service |
| 29 | payload | schema不正拒否tests PASS |
| 30 | unknown | handler0、INTERFACE_ERROR、安全disable |
| 31 | duplicate | 起動時拒否tests PASS |
| 32 | PHASE1 coverage | required 100% |
| 33 | PHASE2 coverage | required 100% |
| 34 | command set diff | 0 |
| 35 | Events | 共通10 Event IDs、Qt queued更新 |
| 36 | ViewModel | Job/CreditLimit/共通runtime変換 |
| 37 | task依存 | backendはGUI typeを参照しない |
| 38 | Phase A/B | 通常生成優先を維持、上限中のみlocal fallback |
| 39 | save isolation | 最終EXE: 21回注入、隔離/overlay/他job完了/復旧PASS、不一致row 0 |
| 40 | Limit live | 既存実Notebook検出/上限中Download/自然解除PASS。最終EXEは実ChromeローカルDOM fixture |
| 41 | Pause live | 既存source実Notebook増分0。最終EXE Pause/Resume/StopのCommand経路PASS |
| 42 | switch live | 実Qt GUI/EXEで両mode切替・別process復元PASS |
| 43 | tests | 最終579 passed / 0 failed / 330.88秒。直前578 suiteもPASS |
| 44 | regression | compileall、diff-check、GUI/settings/browser、Stop/Close、RAW/Ending/HLS/ZIP PASS |
| 45 | Version | Python/Product 1.2.5、File 1.2.5.0、GUI Ver1.2.5 |
| 46 | cleanup | 4対象/834ファイル/1,971,931,082 bytes削減。失敗0 |
| 47 | portable | 既存script exit 0、最終onedir/展開後PASS |
| 48 | EXE dual GUI | 往復・同一runtime・再起動復元PASS |
| 49 | EXE limit | DOM検出・時刻＋5分・cloud calls 0・local backlog PASS |
| 50 | EXE Pause | Pause→Resume→Stop PASS。Stop 6.13秒、Close 3.09秒、所有process残0 |
| 51 | package audit | runtime/profile/credentials混入0。依存ライブラリのTypeScript .d.tsは動画.tsと区別 |
| 52 | commit | 製品Gate PASS後、この文書を含むrelease commitを作成する |
| 53 | push | commit後、origin/mainへ通常push。実行結果は最終報告へ記載 |
| 54 | HEAD/origin | push後にfetch/ls-remoteでSHAを照合し、最終報告へ記載 |
| 55 | ahead/behind | push後0/0を最終確認する |
| 56 | Git status | commit前は今回変更のみ。push後cleanを最終確認する |
| 57 | phase2 | ref f6f5871925f44718f51a106f7f55fa44e8f882b1保持、元3repo変更0 |
| 58 | known issues | source upload recovery / announcement modal live自動dismissは承認済みUNVERIFIED_LIVEを維持 |
| 59 | 未解決重大事項 | 製品Gateでは0。Git反映完了判定は最終照合後 |
| 60 | release readiness | 製品・配布Gate PASS。Git完了を含む最終判定は最終報告参照 |

## 証跡

- `work/v125-final-second-tests.xml`: 579 tests
- `work/v125-exe-final-limit-report.json`: 実Chrome fixture、上限待機/ローカル変換/両GUI/Pause
- `work/v125-exe-gui-restart-0.json`〜`2.json`: 別EXE processの選択復元
- `work/v125-exe-final-isolation-report.json`: 隔離・復旧・即時row更新
- `work/v125-exe-final-shutdown-report.json`: Stop/Close、modal fixtureのdismiss/scrim消失
- `work/v125-final-extracted-portable-report.json`: 日本語/空白path、settings/browser/media/Fake E2E
- `work/v125-final-package-report.json`: ZIP integrity、全408ファイルhash照合、SHA/size
- `work/v125-git-audit.json`: tracked/untracked対象のDB/media/runtime/代表的credential実値混入なし
- 実Notebookの既存証跡: `work/v125-live-gui-002/report.json`、`release-probe.json`、`work/v125-live-local-report.json`

## Cleanup・保護

通常のPowerShell Remove-Itemで削除成功。旧dist Ver1.2.4（701,389,334 bytes）、旧配布ZIP（277,701,293）、初回候補build/ZIP（979,204,201）、PyInstaller中間物（13,636,254）。計1,971,931,082 bytes。削除内容は再生成可能物で、直接削除のためごみ箱からの復元ではなくsource/保持checksum/build手順から再buildする。

cleanup後dist=979,213,097 bytes、build=35,147 bytes。最新版Portable/ZIP/SHA、旧checksum、build用license、試験報告・screenshotsを保持。試験用runtimeと展開検証copyは証跡として保持し、本番RAW/ZIP/HLS/TXT、settings/presets/profile/Git/sourceは削除していない。

実運用Ver1.2.4のsystem JSON 436件（jobsは256件）を最終検証前とcleanup後にhash照合し、変更0。以前の177件manifestの配置変更に関する不明点は実装報告に明記したまま、現在の前後比較証跡と混同しない。

SOURCE_UPLOAD_ERROR_LIVE_RECOVERY: UNVERIFIED_LIVE

ANNOUNCEMENT_MODAL_LIVE_AUTO_DISMISS: UNVERIFIED_LIVE

PRODUCT_AND_PORTABLE_GATES: PASS
