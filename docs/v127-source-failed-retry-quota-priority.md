# Ver1.2.7 — source, live and portable release gates

Instruction: DJD-CHAPPY-V127-SOURCE-FAILED-RETRY-QUOTA-PRIORITY-FULL-001

Base: main `ba0a4cd24654dc9d927109321f795716482d5c8b` (Ver1.2.6).
Source/live, final-version tests, clean ZIP and frozen EXE gates passed on 2026-09-09 before the release commit. Git publication is verified separately in the final local handoff report, `work/v127-final-release-result.txt`.

## Root causes and primary evidence

1. Source failure: `NotebookDomAdapter.source_state()` searched rendered failure sentences and associated tooltips, but not the live row's `single-source-error-container` class or `aria-label="エラー情報"`. It returned PROCESSING whenever a registered source was not ready, even without a processing indicator. The old `ensure_source()` tried a Source Retry button rather than reuploading the TXT.
2. Failed artifact: the poll path raised `remote video generation failed`, which became NOTEBOOK_STAGE_FAILED. Re-submission rejected FAILED as uncertain, and the chat sender rejected any artifact. This prevented snapshot-based recovery of a positively failed artifact.
3. Sticky local phase: quota recheck happened at cycle entry; both local lanes enqueued the entire backlog before waiting. A quota recovery during an active task could not prevent the next queued task from starting. Ending and HLS were also run without an intervening priority boundary.

GNB primary code inspected (read-only):

- `AutoGeminiNoteBookCreator/app/automation/source_monitor.py`: registration/indexing separation, 2-second stable readiness, positive source count, explicit error phrases, `.source-item-more-button`.
- `app/automation/notebook_processor.py`: source readiness before prompt, existing generation check before retry.
- `app/automation/batch_processor.py`: late acknowledgement rechecks to avoid duplicate generation; FAILED_REMOTE handling.
- `app/config/selectors.py`, source-monitor/automation tests, and repository-wide source deletion/retry searches. No reusable per-failed-source deletion implementation was found in the searched GNB app/tests. New scoped deletion uses the observed current UI; no bulk source deletion or Studio Retry action is used.

Live REV018 read-only probe on 2026-09-08:

- Notebook `0ec179a6-d87a-42dd-b14a-9588bf90f2d6`.
- Failed semantic row class and accessible error icon present; old adapter returned PROCESSING.
- Target row `.source-item-more-button` opens a menu containing `ソースを削除` and `失敗したソースをすべて削除`. Only the former is in scope.
- `work/v127-source-probe-002/source-0.html`, screenshots, and `result.json` hold local evidence. No upload/send/delete occurred.
- The probe's old Ver1.2.5 system directory contained zero JSON files. Its `protected_changes=0` is **not** production protection evidence. Subsequent live acceptance hashes all existing production-version system JSONs, asserts a nonempty set, and stores state only in work copies.
- The older Desktop HTML sample path no longer exists. Current live DOM was used instead; REV098 was subsequently confirmed as naturally failed (see live results below).

## Candidate design

- SourceState exposes ABSENT / UPLOADING / READY / FAILED / UNKNOWN; legacy adapter strings are kept at the compatibility boundary.
- Failure is checked before readiness waiting. Timeout is explicitly reclassified. UNKNOWN leaves the wait for error recovery.
- Failed source reupload claims a persistent bounded retry, rechecks identity, selects only that row's source-delete menu item, verifies disappearance, and only then uploads the same TXT. Ambiguous identity or dialog fails closed.
- Failed generation retains notebook identity and the immutable job snapshot. Retry counts and a pre-send turn boundary survive job persistence; current remote generation and delayed correlated reply are checked before another send. Studio Retry is never used.
- Quota rechecks occur on the browser owner thread. Only available FFmpeg worker slots are submitted; no unbounded local executor backlog. Ending completion yields to priority re-evaluation before HLS. Running tasks are not killed.
- A current correlated quota-insufficient reply remains a generation-block trigger per the explicit Ver1.2.6 correction. Almost warnings do not block; local processing and artifact/download capabilities remain independent.

## Gate status

PASS. Earlier focused source/failed-recovery fixtures: 30 passed; initial scheduler/pipeline/limit regression: 84 passed. These are overlapping subsets, not the final full-suite total.

Live recovery results (2026-09-09):

- `work/v127-source-live-002/result.json`: REV018 SOURCE_FAILED → one failed entry removed → one same-TXT upload → SOURCE_READY → one snapshot Chat send → GENERATING. All 1,310 existing production system JSONs unchanged; original TXT unchanged. State is only in the work copy. Notebook and its new generation remain remote.
- `work/v127-failed-live-001/result.json`: REV098 `ba06522b-cd67-4c17-b425-f43b91f4cf13`, failed video → one snapshot resend through central Chat → GENERATING. Studio Retry not called. All 1,310 production JSONs and original TXT unchanged. Work-copy state is separate from the original FAILED job.
- `work/v127-source-live-001` did not mutate the remote: its early pre-hydration source observation was UPLOADING and the strict acceptance condition prevented sending. The next probe waited for artifact/Studio hydration before classifying the Source. It is not claimed as a recovery PASS.
- No natural quota transition occurred during these live trials. The instruction permits clock/capability injection for absent natural conditions. The tests cover Ending and HLS completion → priority refresh → Cloud before the next Local/ZIP. Validated HLS is retained and resumes ZIP without re-encoding.
- The new packaging recovery verifier passed from source and then from the frozen Ver1.2.7 EXE (`work/v127-exe-recovery-001.json`). It uses synthetic Chrome DOM, actual upload input handling, actual central Chat flow and real FFmpeg/HLS/ZIP. It confirms one failed-entry removal, one reupload, one snapshot resend, Studio Retry zero, almost nonblocking, and HLS checkpoint → Cloud → resumed ZIP ordering. This is an EXE fixture/injected-clock acceptance, not an additional Google live quota transition.

Final source suite: 638 passed, zero failures/errors/skips (`work/v127-final-source-tests.json`). After one additional progress-counter regression and version metadata updates, the final version suite passed **639 tests**, zero failures/errors/skips. Its two disjoint shards cover all 46 test files (286 + 353 tests; `work/v127-final-version-tests.json`). `compileall` and `git diff --check` passed.

After source/live gates, metadata was updated to Python/Product 1.2.7, FileVersion 1.2.7.0. The existing PowerShell/PyInstaller onedir build produced `dist/DJDmaker_Ver1.2.7`. The first packaging preflight timed out after 10 seconds on each FFmpeg/ffprobe version query, before PyInstaller started. Direct execution then succeeded; the unchanged build script progressed through PyInstaller and final assets. A repeated release-tree preflight passed every check. This was not an ExecutionPolicy denial; no policy/bypass change was used. The initial startup delay's OS-level cause was not established.

Clean ZIP audit: 408 files, runtime/private-profile files zero, ZIP CRC PASS, all fresh-extraction file hashes match. The fresh Japanese-and-space extraction passed GUI startup, settings save/separate-process restoration, Chrome startup, preset startup-selection reset and Fake E2E through RAW/HLS/ZIP.

## Frozen EXE results

- Recovery fixture: SOURCE_FAILED → targeted removal/reupload → READY; failed video → one snapshot Chat resend → GENERATING; Studio Retry 0; almost warning did not prevent generation. Actual bundled FFmpeg/HLS/ZIP and injected quota recovery proved Cloud before the next Local task and ZIP resume without re-encoding.
- Save isolation: injected per-job save failure did not stop other jobs; reconciliation completed (`work/v127-exe-isolation-001.json`).
- Stop/Close: 6.06 / 3.71 seconds, subsequent navigation 0, owned browser processes remaining 0. Safe modal detect/dismiss/scrim disappearance was tested against fixtures, not a newly observed Google announcement (`work/v127-exe-shutdown-001.json`).
- Real-time scheduler fixture: **603.340 seconds** between wait/rescan events, 100 unfinished jobs retained, Cloud calls 0 while blocked, local and collected-video jobs both COMPLETED with Ending skipped, RAW gate/HLS/ZIP/TXT move PASS. Pause → Start → Stop PASS (`work/v127-exe-limit-001.json`).
- PHASE1/PHASE2 switching, command bindings, shared preset and separate-process GUI selection restoration passed. Screenshots were visually inspected. Browser current-process/profile behavior passed the regression suite; EXE Chrome startup/owned shutdown also passed. Authentication/Chrome-launch code is unchanged; this release did not repeat Fresh Google sign-in.

## Artifacts

- `dist/DJDmaker_Ver1.2.7/DJDmaker.exe`: 4,006,029 bytes; SHA-256 `99093FFBC858EB36F241C7B7522220DC77BE7797B32E03ED7924006C464E18EE`.
- `dist/DJDmaker_Ver1.2.7.zip`: 277,794,358 bytes; SHA-256 `8F71E04D8A86BC0CCA20B22E345A93B2B495A5FFB98C16CD5B04EF339DE13B6F`.
- Python/ProductVersion 1.2.7; FileVersion 1.2.7.0; GUI Ver1.2.7. Checksum file: `dist/DJDmaker_Ver1.2.7_SHA256SUMS.txt`.

## Known limitations and integrity

- Natural quota recovery was not observed in this run. The explicitly permitted injected-clock/capability test is used; it is not mislabeled as a natural Google live transition.
- `ANNOUNCEMENT_MODAL_LIVE_AUTO_DISMISS: UNVERIFIED_LIVE` remains the accepted known issue, with fixture coverage. Historical TAX087's unexplained completion remains unknown; REV018's new, observed recovery does not explain that separate incident.
- All 1,310 protected production system JSONs remained unchanged, including the current Ver1.2.6 runtime's 256 jobs. Original TXT files remained unchanged. Live state was saved only to work copies; remote generations remain available to the normal collection flow.
- Original three repositories remain clean and unchanged; phase2 remains `f6f5871925f44718f51a106f7f55fa44e8f882b1`. No GUI redesign, DB, production migration or authentication-method change was introduced.

## Scoped build cleanup

After EXE/package gates, removed only `dist/DJDmaker_Ver1.2.6` and `dist/DJDmaker_Ver1.2.6.zip`: 409 files, 979,238,492 bytes before → 0 bytes after; failures 0. The exact resolved paths, absence of runtime data/reparse points, and old EXE/ZIP hashes were checked before native PowerShell deletion. Protection hashes for all 1,310 production JSONs and the new EXE/ZIP were checked before and after; changes 0. Ver1.2.7, source/Git/history, phase2, old checksum/release records, user settings/presets/profiles and real media remain. Old binaries were not moved to the recycle bin; they can be rebuilt from the retained Ver1.2.6 commit. Evidence: `work/v127-cleanup-result.json`.
