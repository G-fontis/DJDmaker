# Browser handoff critical fix v0.1.2

Instruction: `DJD-CHAPPY-V012-CRITICAL-BROWSER-HANDOFF-MERGED-001`

## GNBCreator evidence and root cause

The primary source is `AutoGeminiNoteBookCreator` at
`28bd51dfe2894018bfc9d65a02f219a933199127`:

- `app/automation/chrome_login.py`: launches ordinary Chrome with the dedicated
  `--user-data-dir`, has no automation connection, and blocks in
  `process.wait()` until Chrome closes.
- `app/ui/main_window.py` (introduced by commit `86ce7ad`): opens the same
  ordinary Chrome for login and explicitly tells the user to close it before
  Start.
- `app/automation/browser_manager.py`: Start independently calls Playwright
  `launch_persistent_context()` for that profile.
- `app/automation/batch_processor.py`: constructs a new BrowserManager at the
  start of each batch and stops it in `finally`.

Chrome close was therefore required because the login Chrome exclusively owned
the profile while the processing BrowserManager tried to launch a second Chrome
with the same profile. It was a profile-lock and split-lifecycle consequence,
not a NotebookLM requirement. DJDmaker v0.1.1 copied that split: blocking
`run_manual_login()` plus an independent persistent-context launch in the
pipeline factory. Closing removed the profile lock but discarded the live page;
not closing caused the second launch to fail.

## v0.1.2 lifecycle

1. Google Login calls the application-scoped BrowserManager.
2. It starts one ordinary Chrome with the dedicated profile, NotebookLM URL,
   and a loopback-only ephemeral CDP port, then returns without waiting for
   Chrome to close.
3. The user logs in and leaves that Chrome open.
4. Start checks the process, attaches Playwright over CDP, validates context and
   page references, selects an existing NotebookLM page first, and navigates a
   managed/about:blank page when needed.
5. An unrelated user tab is never navigated or closed; a new managed tab is
   created instead.
6. Authentication is checked before pipeline creation. If expired, the GUI gets
   a specific login-required error and the same Chrome remains open for login
   and retry.
7. If Chrome was closed, Start launches the same profile again, preserving any
   Chrome-owned session, then attaches normally.
8. Shutdown/completed processing closes the owned Chrome. Start never launches
   a second Chrome while the managed one is alive.

Playwright attaches in the pipeline thread. This deliberately avoids moving a
Playwright synchronous object between the Qt login worker and pipeline thread;
the handoff identity is the same Chrome PID/profile/session/pages, not a stale
thread-bound Python wrapper.

## Page and safety rules

- Priority: existing `notebook.google.com` page, live managed page,
  about:blank, then a new page.
- about:blank is reusable but never considered ready; it is navigated to
  `https://notebook.google.com/`.
- Wrong-domain tabs remain unchanged.
- Closed pages, inaccessible URLs, stale contexts, and disconnected browser
  objects are discarded and reacquired.
- Browser lifecycle logs contain profile path, liveness, PID, connection and
  context/page counts, sanitized selected URL without query/fragment,
  navigation result, and authentication result. Credentials, cookies, tokens,
  and page contents are excluded.
- Existing Notebook completion polling (`artifact-library-item` and exact
  accessible button name `再生`), delayed Angular DOM handling, 12-part RAW
  gate, artifact-only deletion, refresh/non-revival check, Ending, HLS and ZIP
  behavior are unchanged.

## Regression coverage

Tests cover login/browser reuse, login-to-Start handoff, no-close operation,
about:blank navigation, wrong-domain preservation, existing Gemini page reuse,
stale page/context recovery, closed-browser fallback, expired sessions,
duplicate launch prevention, profile-lock errors, repeated Start, and Japanese
and space-containing profile paths. The existing engine and safety suites remain
enabled.

No source repository other than DJDmaker is modified. GitHub push is prohibited
for this candidate.

## Live acceptance evidence (2026-09-06)

- Source GUI: Login launched Chrome root PID 21236. Start attached to that same
  root process; only renderer child PIDs changed.
- Diagnostic TXT `DJD_BROWSER_HANDOFF_001.txt` completed Notebook creation,
  source upload, Video generation/monitoring, download, RAW gate, artifact-only
  deletion, Ending, HLS and ZIP. Job ID:
  `e8d02221e6254719996e9960e3a50084`.
- Notebook ID: `0be4bf9d-a804-43b7-98fb-8165bc60670e`. A post-completion reload
  found zero `artifact-library-item` video artifacts while the Notebook URL and
  source name remained present.
- The RAW file remained at 6,668,197 bytes, duration 54.102494 seconds, H.264
  video and AAC audio. All 12 persisted safety-gate fields are true.
- Ending result and HLS result are PASS. The 3,337,795-byte ZIP passes CRC,
  contains the playlist and nine TS segments, and every entry is ZIP_STORED.
- Real Chrome cases also passed: about:blank navigation without PID change,
  unrelated tab preservation with a new managed page, same-profile restart,
  and expired-session login request while keeping Chrome alive.
- Portable v0.1.2 was exercised under a Dropbox/Japanese/space path. Login root
  PID 18816 remained the only root Chrome after Start and was closed on app
  shutdown. A clean portable copy also passed GUI, FFmpeg/ffprobe, browser,
  settings restart and Fake E2E verification.
- Full source suite: 218 tests passed (the original 204 plus browser handoff
  regression coverage).
