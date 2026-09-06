# STATE — resume pointer

**Focus:** New feature "OBS Switcher" — page to build/protect/backup/test the
laptop⇄Samsung-tablet OBS scene-switching setup (from obs.md).

**Last decision:** Spec + implementation plan both approved & committed.
Spec: `docs/superpowers/specs/2026-09-06-obs-switcher-design.md`
Plan: `docs/superpowers/plans/2026-09-06-obs-switcher.md` (12 TDD tasks)
Choices locked: chattr +i lock on stable files; automation-layer-only build
(no package installs); auto-read WS password from OBS config.json; verify scenes
only (never edit them).

**Next action:** Execute the plan task-by-task (subagent-driven or inline).
Not started yet — Task 1 first.

**Files to create:** core/obs_switcher.py, ui/obs_switcher_page.py,
tests/test_obs_switcher.py; edit assets/cleanmint-helper (obs-lock/obs-unlock),
ui/main_window.py (nav entry), CLAUDE.md.

**Hints:** —
