# Ver1.2.8: collision, lifecycle and current Chrome state

Instruction: `DJD-CHAPPY-V128-STARTUP-COLLISION-STOP-REASON-CURRENT-WINDOW-FULL-001`

Base: main `455d6121c1cf08ebb032270b4edb3740f3149b07` (Ver1.2.7, 639 tests).

## Evidence and root causes

The production Ver1.2.7 snapshot contains 197 jobs: 101 COMPLETED, 77 FAILED
with OUTPUT_NAME_COLLISION, and 19 WAITING_VIDEO. There are exactly 77 repeated
normalized-source groups. The supplied IDs both refer to the same current TXT:
`JBCNG04_第04話_AIに任せていい仕事、ダメな仕事.txt` (the actual filename includes J).
Duplicate `06f5585c321e4598969caa9a43995297` has no notebook, generation, RAW or
ZIP. Owner `c5d0f3f1978e4c09ba60f8f87c30570b` is COMPLETED with existing media.
Their creation times differ by about 163 ms.

The old GUI reload used an unguarded read-existing / create UUID / save sequence.
Two concurrent reloads can both miss a source and import it. The old implementation
was reproduced with synchronized concurrent reads: one TXT produced two job IDs.
Case-sensitive path comparison was a second identity weakness on Windows.
`PipelineCoordinator._reject_output_name_collisions` then grouped by case-folded
stem, preferred the completed/older job, and failed every other job without
reconciling duplicate identity or actual current output paths. This happens before
remote processing. It is called by pipeline lanes, not directly by GUI construction.
Startup migration/display can expose an already saved failure; evidence does not
prove that merely constructing the old window saved new collision failures.

Old errors stayed FAILED: migration could label a no-remote/no-RAW collision
FATAL_FAILED, `_idle_reason` excluded it, and deferred recovery also preserved the
collision. There was no reconciliation that removed the false ownership claim.

The old terminal/discovery logic accepted generic FATAL_FAILED as final and the
GUI fallback accepted all FAILED/deferred jobs as complete. These are demonstrated
unsafe exit conditions. **The exact historical user stop event remains unknown**:
there is no retained production execution log, and the 19 WAITING_VIDEO records
would not themselves satisfy the old complete predicate. Do not conflate this
code defect with proof of the specific historical stop event.

## Current ownership and identity

There is no new persistent stem registry. Current JobRepository records are
reconciled as a complete snapshot before saves. Normalized absolute Windows source
path, job ID and available SHA-256 distinguish same-source imports from distinct
sources. Import discovery and reconciliation share a process-local path-keyed RLock.
Completed output only claims its actual existing ZIP at the current final path.
Missing records, missing-source/no-work records, released terminal failures and
completed records in another/missing output location do not reserve that target.
A name that looks like a test is not by itself evidence that a live job is inactive.

An unused same-source duplicate remains under its original ID as an explicit
inactive `duplicate_of_job_id` reference. It is not deleted, falsely marked
COMPLETED, or generated a second time. Its former collision failure is cleared.
The canonical job's checkpoint and existing media are preserved. Different known
source hashes are not merged. A same-path re-scan reuses the existing identity;
it does not overwrite completed media or silently create a new job for edited TXT.
To submit a genuinely different script safely, use a distinct source/output name.

Real distinct-current-job conflicts retain OUTPUT_NAME_COLLISION with
OUTPUT_BLOCKED, conflicting job ID and a Japanese explanation. Only that job is
blocked; other jobs continue. When a claim is no longer valid, its original ID
returns to saved checkpoint, safe RAW, remote recovery, or pre-notebook WAITING.
No automatic retry loop competes for the same output name.

## Completion and Stop Reason

Final Discovery reloads current records before every automatic completion.
Only COMPLETED and explicitly retry-exhausted TERMINAL_FAILED end a valid job.
Explicit adapter SourceRetryExhausted/GenerationRetryExhausted persists the
corresponding exhaustion counter. Duplicate references are not executable jobs;
an orphan/cyclic reference prevents completion rather than hiding unknown work.
Unreadable records, deferred saves, unknown/fatal-but-unexhausted states, future
deadlines and cloud limits keep completion false. No current runnable task means
wait/rescan, not successful termination. Existing quota-independent Local and
Download priorities remain unchanged.

StopReason stores a code and Japanese message: ALL_TASKS_COMPLETED, USER_STOP,
APP_CLOSE, PAUSED, NO_RUNNABLE_TASK_WAIT, TERMINAL_GLOBAL_ERROR, AUTH_REQUIRED,
BROWSER_CONFLICT, CONFIG_FATAL, STORAGE_FATAL, RECOVERY_CHECK_FINISHED.
Pause/Wait are not final stops. Both GUIs share LifecycleViewModel for current
state, final stop reason, last selected task and next check. Stop reason persists
through GUI switching; the status bar and log also show the human explanation.

## Chrome current state: findings, not an invented regression

At the base commit, `BrowserManager.auth_process_alive` already used the current
owned process's `poll()`, not close/open history. Current profile-lock and automation
checks were retained. No history-dependent current-state branch was found in the
searched production code. Two obsolete diagnostic assignments
`auth-chrome-opened` / `auth-chrome-closed` were removed; they were not decision
inputs. The compatible `auth_chrome_closed` preflight result key represents a
current check, not history. AST contracts prohibit reconnecting historical state.
The exact immutable rule is recorded in DEVELOPMENT_RULES.md.

## Acceptance evidence and release status

Production-copy reconciliation: `work/v128-production-copy-003/result.json`.
77 false FAILED records recovered as duplicate references; IDs/source paths
preserved; 0 current real conflicts; 0 stale active claims in this particular
production snapshot. Stale/self/real-conflict cases are covered separately by tests.
All 2,043 protected production system JSON hashes were unchanged. Remote/media
operations in this copy trial: zero.

Source GUI/current Chrome: `work/v128-source-lifecycle-008.json`.
Actual ordinary Chrome window/process OPEN with stale CLOSED history and CLOSED
with stale OPEN history; history file ignored. Only the acceptance-owned Chrome
was closed. No Google login, generation or artifact mutation was required.
The GUI controller ran an isolated fixture workload with unfinished jobs,
retry-exhausted single-job error, wait/rescan, Pause/Resume, User Stop, all-completed
and App Close. The source verifier uses a shortened wait; formal EXE acceptance
must use the full 600 seconds. This is GUI/current-process live evidence with a
fixture pipeline, not a new Google live generation test.

An initial verifier captured switched widgets before Qt's native show/repaint;
the verifier now waits for visible children. Visual review also found the four-line
lifecycle label could be compressed at the Phase1 minimum window size; a minimum
height was added, Phase1's window minimum accounts for the new rows, and Phase2's
new explanation is above the long existing diagnostics. Revision 008 passed a
visible-region assertion and visual review in both layouts. Revisions before 008
are not used as the final visibility gate.

Source suite: **694 passed**, 48 files, two disjoint shards 324 + 370, zero
failures/errors/skips (`work/v128-source-002-tests.json`). Additional final GUI
regression: 51 passed. The earlier intermediate suite found an exhaustion
classification regression (fixed by persisting adapter exhaustion) and one slow
FFmpeg cancellation run; the unchanged cancellation threshold subsequently passed.
Repository safety checks now prune exactly the previously excluded directories
before traversal instead of walking large excluded acceptance caches first.

After those source/live gates, metadata was updated to Python/Product 1.2.8,
FileVersion 1.2.8.0 and GUI Ver1.2.8. The first in-progress build was deliberately
stopped when a remaining old GUI title constant was found; it was not a policy
denial and no candidate from that attempt is distributed. The title was corrected
and the same formal build script restarted. These interrupted intermediate
attempts are retained as diagnostic history, not as release evidence.

Final boundary review also aligned ownership release with proven exhaustion:
an old TERMINAL_FAILED label without the persistent retry counter must not release
an output claim that discovery could still retry. Its added test passed, and the
fixed/version-aligned full suite passed **695 tests** (324 + 371,
`work/v128-frozen-002-tests.json`). Version-literal expectations in GUI/packaging
tests were updated rather than weakening those checks.

Packaging verification found another concrete issue: the original spec's
`optimize=1` removes Python assertions, including assertions inside frozen
acceptance modules. The onedir/PowerShell build method is retained, but bytecode
optimization is now `0` so those checks and source-tested invariants remain active.
The lifecycle verifier also explicitly rejects optimized execution before creating
its runtime (`python -O` returned 3), and records `assertions_enabled` in its report.
Build/entrypoint/ownership/lifecycle focused regression: 66 passed. Interrupted
pre-fix builds are not distributed. The final build and final full-suite rerun
use this setting; no force/rebase/reset/amend or policy bypass is involved.

## Final build facts

- Final fixed-source suite: **695 passed**, 48 files, 324 + 371, zero failures,
  errors or skips (`work/v128-frozen-003-tests.json`). compileall and diff check PASS.
- Formal PowerShell/PyInstaller onedir build PASS, Python/Product 1.2.8,
  FileVersion 1.2.8.0, GUI Ver1.2.8. No ExecutionPolicy rejection or bypass.
- EXE: `dist/DJDmaker_Ver1.2.8/DJDmaker.exe`, 4,036,675 bytes.
  SHA-256 `8D888EAF9486E45C360304BD0413DC00C4439FEA389CFF8B54B1E784DB880919`.
- ZIP: `dist/DJDmaker_Ver1.2.8.zip`, 277,830,985 bytes.
  SHA-256 `0445BD5FCA8A44A1D5A1D805454F2EA061C1BD1AADE7381EEC4CC71B189CC928`.
- Clean package: 408 files; runtime/private profile files 0; ZIP CRC PASS;
  all files in the fresh Japanese-and-space extraction have matching hashes.
- Fresh extraction: GUI/title, settings write/separate-process restore, browser
  smoke, preset startup reset and Fake E2E RAW/HLS/ZIP/artifact gate PASS
  (`work/v128-fresh-portable.json`). The distribution itself remains clean.
- Frozen recovery fixture: source retry, failed-video snapshot resend, Studio
  Retry 0, almost nonblocking, injected quota recovery priority and HLS/ZIP resume
  PASS (`work/v128-exe-recovery-001.json`). This is synthetic DOM/clock with real
  bundled media processing, not another Google live generation.
- Frozen job-save isolation PASS. Frozen Stop/Close PASS: no live worker or owned
  child remaining and no post-stop navigation; safe modal fixture dismissal PASS.
  Natural Google announcement dismissal remains UNVERIFIED_LIVE.
- Build source/config fingerprints unchanged after packaging; protected production
  system JSONs 2,043, changes 0; original three engines clean; phase2 ref unchanged.

## Release gate result

PASS before commit/publication. Frozen lifecycle acceptance recorded enabled
assertions and **600.064709 seconds** between unfinished-job scans. Current Chrome
OPEN/CLOSED versus opposite historical records, startup duplicate reconciliation,
Pause/Resume, single-job error continuation, User Stop, all-completed, App Close,
and visible stop messages in both GUIs passed. Evidence:
`work/v128-exe-lifecycle-001.json` and its screenshots.

The frozen real-pipeline quota fixture recorded **604.521809 seconds** between
scans, with 100 queued jobs held without generation. Local and collection jobs
both reached COMPLETED, with RAW gate, optional Ending skip, HLS/ZIP and TXT move.
Collection was exactly one check followed by one download. Shared preset,
Pause/Resume, reset time + 5 minutes and both GUIs passed. Three further EXE runs
proved GUI selection restoration across restarts. Evidence:
`work/v128-exe-limit-001.json`, `work/v128-exe-gui-{0,1,2}-001.json`.

Cleanup removed only repository-local `dist/DJDmaker_Ver1.2.7` and its release ZIP:
2 targets, 409 files, **979,266,733 bytes** before and 0 bytes after. Before deletion,
all files were matched against the known old release ZIP, runtime files were zero,
and reparse points were rejected. Original commit, release documents, checksums,
production installations, production data and the new Ver1.2.8 package remain.
These are permanent deletions of regenerable build artifacts, not a trash move.

The full 50-item handoff with the actual final commit, push and remote SHA checks
is generated after publication at `work/v128-final-release-result.txt`. Git uses
only a normal main commit/push; no force, rebase, reset or amend.

Known evidence limits remain explicit: no retained log proves the user's exact
historical stop event; base current-window code did not contain a history-dependent
decision; natural Google announcement dismissal is still UNVERIFIED_LIVE. Neither
missing historical evidence nor fixture tests are presented as a new Google live
generation or natural-quota event.
