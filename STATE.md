# STATE — resume pointer

**Focus:** OBS Switcher feature — DONE + verified end-to-end on this machine.
All merged to `main`; `main` is ~13 commits ahead of `origin/main` (not pushed).

**What shipped:** `core/obs_switcher.py` + `ui/obs_switcher_page.py` +
`cleanmint-helper` obs-lock/obs-unlock + sidebar entry. Spec/plan in
`docs/superpowers/`. `test_obs_switcher.py` 95/95, `test_ui_imports.py` 15/15.

**Fixes made during user testing (all merged):**
- c8e4c95 — polkit prompt nagged every launch on failed/cancelled install;
  now `policy_signature()`-gated, asked once per asset version.
- 2f54a09 — Build failed "Operation not permitted" when protection (chattr +i)
  was on; `build()` returns `needs_unlock`, page offers unprotect→repair→re-protect.
- cb15b9f — `obs_running()` missed Flatpak OBS (bare `obs` process); now
  `pgrep -x obs` too; self_test precondition is just `websocket_reachable()`.
- (latest) — Build no longer warns about WebSocket when it is already enabled;
  only warns when genuinely off + OBS running (can't safely patch a live OBS).

**Verified:** Test Switching passes in the app (Connect/Laptop/Tablet/Restore ✓).
Protect/Unprotect work (rm refused while locked). check_status all green.

**Pre-existing failures (NOT this work, also on old main):**
`test_backend_phase5.py` "Source field valid"; `test_integration.py`
"Would delete" wording (+ a /tmp race from a concurrent process).

**"Set Up Scenes" (merged, then reworked for the tablet):**
- Laptop scene: `pipewire-screen-capture-source` (one portal pick, token persists)
- Tablet scene: `scrcpy --v4l2-sink=/dev/video42` → `v4l2_input` source. NO Wayland
  portal, survives restarts. Helper op `obs-v4l2` loads v4l2loopback +
  boot-persistence. New `tablet_feed` status check.
- VERIFIED on real hardware: live tablet screen captured in OBS (screenshot).
- `test_obs_switcher.py` now 111 checks.
- NOTE: helper changed again → app re-prompts to update polkit helper on launch.

**Fixed (merged):** installer made up to 5 pkexec prompts (mkdir/tee/chmod ×N)
→ now ONE `pkexec sh -c` writes both files from a base64 stdin payload.
Despite the bad UX, the user's partial install DID land the obs-v4l2 helper;
`is_policy_installed()` is True, v4l2 device + persistence files in place.

**Current machine state:** policy installed ✓, v4l2 ready + boot-persistent ✓,
scrcpy feed NOT running (user clicks "Set Up Scenes" to start it — no password
prompt now since v4l2 already prepared).

**Next action:** User clicks "Set Up Scenes" → tablet feed starts, appears in OBS.
`git push` when ready (~21 commits ahead of origin/main).

**Hints:** —
