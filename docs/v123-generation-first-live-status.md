# Ver1.2.3 candidate: generation-first / live row refresh

Instruction: `DJD-CHAPPY-V123-GENERATION-FIRST-CLOUD-THEN-LOCAL-LIVE-STATUS-REFRESH-FULL-001`

## Implementation

The starting main HEAD is `9bd93008ed073ebe2fdb5f5b3f6a8dd674453fd0` (Ver1.2.2).
Previously each job could enter Download/local conversion before later jobs were dispatched, and the GUI received whole-list updates at cycle boundaries.

Phase A (`GENERATION_DISPATCH`) now checks and acts on each unstarted/retryable job, persists generation/reservation/ready or a reasoned terminal outcome, then advances. It performs no Download, Ending, HLS or ZIP. Already generated, reserved, ready, completed and local-media checkpoints need no new dispatch. Fatal or exhausted retries do not hold the phase forever.

Only after no dispatch work remains does Phase B (`COLLECT_LOCAL`) start. It respects persisted `next_poll_at` (the existing equivalent of next_check_at), reschedules unfinished video and collects ready artifacts through the existing 12-point RAW gate, artifact-only deletion, optional Ending, HLS, ZIP, COMPLETED and TXT move. Notebook and source deletion remain forbidden.

Each stage/state is saved atomically before emitting a detached Job snapshot. A Qt queued connection updates only the matching job_id row, then summaries; runtime events accompany the same stage. Presentation stage/phase/revision fields are additive and reload from JSON. A failed save cannot emit a successful row update. Per-event runtime summaries use saved snapshots instead of rereading all job JSON files. Workers do not wait for GUI drawing.

Selection, checkboxes, sort and scroll position are retained. Detailed Download/RAW/reservation labels are not hidden by coarse states. The current-job summary follows the runtime job, not the first active row. Notebook completion is shown at generation acceptance/reservation confirmation. White GUI, button actions and browser launch/auth method are retained; no Phase2 integration.

## Source/fixture evidence

- Original 444-test baseline retained. Final source run: **495 passed**, 240.42 seconds (`work/v123-full-04`). After version metadata update: **495 passed**, 290.38 seconds (`work/v123-release-tests`). compileall and git diff --check PASS.
- 100-job fixture: 80 new + 10 generating + 5 ready + 5 reserved. All 80 submits precede the first download/local conversion; Phase A Ending/HLS/ZIP counts are 0/0/0. Five ready jobs complete in Phase B.
- Worker #21 is deliberately blocked while the GUI already shows #20 generating. 175-row sort/checkbox/scroll/selection test passes. Save failure, GUI-thread affinity, late close event and per-stage queued refresh are covered.
- Source GUI/media smoke: `work/v123-source-ab-smoke-report.json`; two dispatches before two collections, 54 row updates, real FFmpeg/ffprobe/HLS/ZIP, optional Ending skip, RAW gates and TXT move PASS. Latest source is rechecked before release.

## Live evidence

Isolated runtime: `work/v123 live AB plqqqr7z`. Original production 177 JSON files are read/hashed, not modified. Warm dedicated profile is reused; this is not claimed as Fresh authentication.

- A: `7bd048837bb2446eb9f11cd28c94ebc3`, Notebook `518be343-3d34-4050-9f53-9b024201fa0b`.
- B: `bed88df32bfd4975a6b7ae886b3e794e`, Notebook `d565e886-09e9-4435-b296-17732043cb7e`.
- Existing TAX120 copy: `3d5f3ef52536445a8093dcc9877de108`, Notebook `2a42754c-d5c7-4695-add9-bec2597500bb`.

First live session sent the selected snapshot once to each new Notebook. A generation accepted at elapsed 45.937 seconds; B at 86.982 seconds; Phase B started at 87.815 seconds. No Phase A Download/local processing occurred. `report.json` records 61 GUI updates, 0 display mismatches, maximum save-to-row latency 0.047023 seconds, no reported errors, unchanged original 177 JSON files and owned browser closed.

TAX120 returned NOT_STARTED during current inspection despite its historical accepted checkpoint. This is not claimed as ready or successfully collected. No blind regeneration is performed. The new A/B jobs retain their original due times.

The first GUI was closed through normal WM_CLOSE to exercise safe shutdown and load final presentation corrections. The same copy checkpoints are resumed without new Notebook creation or Preset resubmission. A harness-only duplicate preset-name error on the first restart attempt was corrected by selecting the already existing test preset; that failed harness startup performed no remote work.

Live collection result: PASS. The latest source resumed directly in Phase B, with no Notebook creation or chat submission. A reached its original due time, was detected ready, and was downloaded immediately. `collection-report.json` records RAW all 12 gates true, artifact deletion (including the existing refresh/absence verification), Ending `SKIPPED (not configured)`, HLS PASS, ZIP, COMPLETED and TXT `MOVED`. Stop was requested synchronously at completion; B was not newly processed after Stop. Owned browser closed and production 177 JSON remained unchanged.

Collection GUI: 34 row updates, 0 display mismatches, maximum latency 0.023954 seconds. RAW: `raw_files/DJD_V123_LIVE_A.mp4`, 4,233,943 bytes, duration 371.774694 seconds, SHA-256 `E545B4588E1AE251229C99E78AD4D919BFD2B82BDBF912FBAF65ECACF3877016`. ZIP: `output/DJD_V123_LIVE_A.zip`, SHA-256 `AFF5744B5D2E293199C9C06D8DE6A3A9D7D02D87AEF2E62612EF6A3452026387`. These files are private acceptance data, not package/Git content. TAX120 and B retain WAITING_VIDEO copy checkpoints; no remote Notebook/source was deleted.

Quota/reservation is fixture-covered; quota shortage did not naturally occur in the new dispatches and is not claimed as a new live result. No intentional quota exhaustion is performed. A naturally absent modal is not forcibly recreated. Latest source GUI/media smoke `work/v123-source-ab-smoke-report-02.json` also passed with 54 incremental updates.

## Portable release gate

Version was changed only after source/test/live PASS. Python/Product 1.2.3, FileVersion 1.2.3.0, GUI Ver1.2.3. Existing PyInstaller 6.22.2 onedir spec and Qt/Playwright/FFmpeg assets were retained. Because the environment refuses PowerShell script execution, the equivalent existing PyInstaller CLI was run with a fresh `build/pyinstaller-v123` work directory; no execution policy was changed and no rejected cleanup was bypassed.

- Portable: `dist/DJDmaker_Ver1.2.3`; EXE 3,900,537 bytes.
- EXE SHA-256: `8F5D0B7F31C9F285110EFCEB3D9C6399E447F379D14B22FD1A18349469E00111`.
- ZIP: `dist/DJDmaker_Ver1.2.3.zip`; 277,678,858 bytes.
- ZIP SHA-256: `3CB9F3E729C253E92D92918F61F24E922830CD10962EE17813F7FDB4BDDC715E`.
- Checksums: `dist/DJDmaker_Ver1.2.3_SHA256SUMS.txt`. New code/version changes explain different hashes from Ver1.2.2.
- Pristine package audit: 408 files, profile/credential/runtime-media files 0; ZIP integrity PASS. Tests run on separate copies, not the pristine distribution.
- Japanese/space path extraction: `work/v123 配布検証 q1y0zncm/DJDmaker_Ver1.2.3`.
- `work/v123-portable-verified.json`: EXE GUI, FFmpeg/ffprobe without development PATH dependence, settings write/new-process read, browser local smoke, preset CRUD/blank selection after restart and Fake E2E PASS.
- `work/v123-exe-sequential.json`: two submits precede both downloads; 54 incremental GUI updates; both jobs COMPLETED, RAW gates true, Ending skipped, HLS/ZIP PASS and TXT MOVED. Remote adapter is fake in this EXE smoke; Qt and media tools are real. Real Google evidence is the source live run above; no claim of extra EXE Google generation is made.
- `work/v123-exe-shutdown.json`: Stop 3.440 seconds, Close 1.075 seconds; worker false, owned processes 0, navigation before/after 0/0. Local modal fixture detects/dismisses/scrim disappears; this is not live Google modal evidence.
- `work/v123-portable-migration-report.json`: 175 private copies (75 COMPLETED, 100 FAILED), IDs/Notebook/preset snapshots preserved; production writes 0.

## Audit / cleanup

`work/v123-final-audit.json` confirms all 177 production JSON match their preserved baseline, tracked media/DB files 0 and detected actual secret files 0. Secret-related words in implementation/documentation are not credentials. Runtime profiles, videos, logs, acceptance state and temporary scripts remain ignored and are excluded from the package. Original engine repositories were not modified.

Prior cleanup commands were refused by environment policy; this run does not retry/bypass those refused destructive operations. Old regenerable releases are retained under the existing exception, not treated as user data or included in the new ZIP. No user data was deleted. Measured final dist: 5,862,177,163 bytes; build: 40,043,991 bytes. Old portable folders retained: Ver1.1 701,277,060; Phase2 701,304,622; Ver1.2 701,303,594; Ver1.2.1 701,332,901; Ver1.2.2 701,354,640 bytes. Current Ver1.2.3 folder: 701,366,883 bytes. Phase2 remains untouched.

Known inherited observations:

- `ANNOUNCEMENT_MODAL_LIVE_AUTO_DISMISS: UNVERIFIED_LIVE` (fixture coverage; user permitted).
- `SOURCE_UPLOAD_ERROR_LIVE_RECOVERY: UNVERIFIED_LIVE` (user permitted; cause of earlier completion remains unknown).

## Required 51-field report

This is the pre-push release gate record; final commit/remote receipt is reported after normal push.

| # | Item | Result |
|---|---|---|
| 1 | HEAD before | 9bd93008ed073ebe2fdb5f5b3f6a8dd674453fd0 |
| 2 | Old pipeline | Per-job dispatch then local work before later dispatches |
| 3 | Phase A | Check → dispatch/reserve → persist → next |
| 4 | Phase B | Due check → collect → RAW → optional Ending → HLS/ZIP |
| 5 | Phase A targets | New/uploading/retryable failed/interrupted dispatch |
| 6 | Phase A completion | No remaining dispatch work; fatal/retry-exhausted have explicit reasons |
| 7 | Phase B start | Only after Phase A completion |
| 8 | Local start | Phase B only |
| 9 | Old GUI | Whole-list update at cycle boundaries |
| 10 | New GUI | Detached snapshots, queued Qt signal, incremental row |
| 11 | Ordering | State → atomic save success → signal → GUI |
| 12 | Thread safety | GUI slot only; worker never accesses widgets |
| 13 | Row identity | job_id lookup, not fixed row index |
| 14 | Notebook | Create/open/acceptance events update immediately |
| 15 | Source | Upload/ready/error events covered |
| 16 | Preset | Send/reply events, real A/B live send |
| 17 | Generation | Accepted and WAITING_VIDEO immediate |
| 18 | Quota/reservation | Immediate fixture-covered labels; natural new quota not observed |
| 19 | Download | Start and completion immediate; live PASS |
| 20 | RAW | Validation/save immediate; 12 gates live PASS |
| 21 | Ending | Optional skip retained; immediate labels |
| 22 | HLS | Start/complete labels and column; real tool PASS |
| 23 | ZIP | Actual zip.start marks ZIPPING, not premature HLS start |
| 24 | Completed | Immediate completion, TXT moved; no regeneration |
| 25 | Stop | Immediate control overlay; safe stop/close PASS |
| 26 | Aggregates | Recomputed after row update using persisted snapshots |
| 27 | Runtime | Job/phase/stage/URL/counters synchronized |
| 28 | Sort | PASS |
| 29 | Checkbox | PASS |
| 30 | Scroll | PASS |
| 31 | Selected row | PASS |
| 32 | Latency | Live max 47.023 ms dispatch / 23.954 ms collection |
| 33 | Phase A Ending | 0 |
| 34 | Phase A HLS | 0 |
| 35 | Phase A ZIP | 0 |
| 36 | Duplicate generation | 0; resumed copy did not resubmit |
| 37 | Completed regeneration | 0; regression PASS |
| 38 | Fixture | 100-job dispatch-first, #20 visible during #21, 175-row preservation PASS |
| 39 | Live GUI | 3 copies, 2 dispatches then 1 complete collection; 95 row updates, mismatches 0 |
| 40 | Tests | 495 passed; compileall/diff check PASS |
| 41 | Production JSON | 0 changes / 177 |
| 42 | Version | 1.2.3 / 1.2.3.0 / Ver1.2.3 |
| 43 | Build | Existing PyInstaller onedir PASS |
| 44 | Portable | Unicode/space path, settings, browser, media, migration PASS |
| 45 | EXE Phase A/B | PASS, two submits before any download |
| 46 | EXE row refresh | PASS, 54 updates with real Qt/media and fake remote |
| 47 | Commit | Normal release commit containing this record; final SHA in completion receipt |
| 48 | Push | Only normal git push origin main after these gates; final result in receipt |
| 49 | HEAD/origin after | Verify equal and ahead/behind 0/0 after push |
| 50 | Phase2 | Unchanged at 46c4599e76383b663552aec3f052e0dca8cc09f9; no Phase3 |
| 51 | Unresolved | No new blocking implementation issue; inherited live observations and cleanup retention above |

TAX120's current NOT_STARTED observation remains unexplained; its checkpoint is preserved, not falsely marked complete. New B remains remotely generated/uncollected and can be recovered from the saved copy later. Acceptance RAW/ZIP and both checkpoints must be retained.

Source/live/portable release gates: **PASS**. Git transport completion is conditional on the final remote verification receipt.
