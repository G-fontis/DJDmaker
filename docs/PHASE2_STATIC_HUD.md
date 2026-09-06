# Phase 2 Static HUD

Phase 2 is the presentation-only branch for the Ver1.1 functional baseline. It introduces a static control-room HUD while preserving the existing application, controller, scheduler, adapter, persistence, browser, credit, reservation, recovery, media, and packaging behavior.

## Layout

- Header: product identity, three-engine caption, creator credit, and static technical markers.
- Left sidebar: all nine existing actions in their required order. Start, recovery, and stop have distinct static roles.
- Center: job queue, seven-step pipeline, and embedded sanitized execution log.
- Right dashboard: actual job totals, current job, browser/auth presentation state, credit/reset/reservation values, configured paths, and real disk-free information.
- Dialogs: Settings, Preset, Job Detail, and Log use the same dark navy/cyan HUD palette.

## Safety boundaries

- No timers, animations, pulses, moving gradients, particles, or background motion are used.
- No sample values are injected during normal startup.
- Preview values live only in `djd_maker.testing.hud_preview` and are never imported by the production composition root.
- Embedded and detached logs use the existing redaction rules before display.
- Authentication panels never display a Google email address or credentials.
- Transient persistence retries remain log-only; the HUD adds no modal retry notification.

## Visual acceptance

Run from the repository root on Windows:

```powershell
$env:QT_QPA_PLATFORM = 'windows'
$env:PYTHONPATH = 'src'
python -m djd_maker.testing.hud_preview docs/screenshots/phase2
```

The renderer uses temporary, isolated files for the enabled-state preview and produces main-window images at 1920×1080, 1600×900, and 1280×720 plus Settings, Preset, Job Detail, Log, Credit-exhausted, and reservation/recovery states.

Phase 3 animation work must not begin until this Phase 2 branch and its screenshots are explicitly approved.
