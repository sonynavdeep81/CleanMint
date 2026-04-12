# CLAUDE.md — CleanMint Project Notes

## Project
CleanMint: A commercial-grade Linux system cleaner for Ubuntu, built with Python 3.12 + PyQt6.
See PLAN.md for full architecture and phased implementation plan.

---

## Current Working State (as of 2026-04-12)

### What is fully working
- **Cleaner page**: Scans 10 categories, shows sizes, checkbox selection, preview dialog, real clean
- **Analyzer page**: Large files, large folders, duplicate detection (hash + name+size), broken symlinks
- **Dashboard**: Disk usage bar, junk summary, health score, junk breakdown list, navigate-to-cleaner button
- **Health page**: 6 checks (disk, broken packages, failed services, old kernels, journal, pending updates)
  - "Run apt upgrade" opens a streaming live-output dialog (no terminal needed)
  - "Show & Restart" buttons on failed services row using pkexec
- **Startup page**: Lists startup apps and services
- **Settings page**: Dark/light mode, persistent JSON settings
- **Logs page**: Shows CleanMint session logs
- **Polkit policy**: Installed at `/usr/share/polkit-1/actions/org.cleanmint.policy`
  - Covers: journalctl, snap, apt-get, systemctl — all with `auth_admin_keep` (one password per session)
  - App detects if policy is missing/outdated and offers to install/update it on launch
- **Tests**: 81/81 passing across 4 test suites

---

## Installation & Running

### On this machine (already set up)
CleanMint is installed and ready. Launch it by:
- Pressing the **Super key**, typing "CleanMint", clicking the icon, OR
- Running `cleanmint` in a terminal

If the icon is missing, re-run the desktop entry install:
```bash
cp ~/Cleanmint/cleanmint/assets/cleanmint.desktop ~/.local/share/applications/cleanmint.desktop
update-desktop-database ~/.local/share/applications/
```

---

### On a new machine — full install from scratch

**Prerequisites:** Ubuntu 22.04+ (or any Debian-based distro), Python 3.10+

**Step 1 — Get the files onto the new machine (pick one method):**

Option A — Copy via USB / zip:
```bash
# On this machine — create a zip (venv excluded, it gets recreated):
cd ~ && zip -r cleanmint.zip Cleanmint --exclude 'Cleanmint/venv/*' --exclude '*/__pycache__/*'
# Copy cleanmint.zip to USB or transfer it, then on the new machine:
unzip cleanmint.zip -d ~
```

Option B — Copy over the network (SSH):
```bash
# From this machine, push to the other machine:
scp -r ~/Cleanmint user@NEW_MACHINE_IP:~/Cleanmint
```

Option C — GitHub (best for ongoing reuse):
```bash
# On this machine — push to GitHub once:
cd ~/Cleanmint
git init && git add . && git commit -m "CleanMint initial release"
git remote add origin https://github.com/YOUR_USERNAME/cleanmint.git
git push -u origin main

# On any new machine — clone and install:
git clone https://github.com/YOUR_USERNAME/cleanmint.git ~/Cleanmint
```

**Step 2 — Run the installer (on the new machine):**
```bash
cd ~/Cleanmint
bash install.sh
```

The installer automatically:
1. Checks Python 3.10+ is available
2. Creates a Python virtual environment (`venv/`)
3. Installs all dependencies: PyQt6, psutil, reportlab, send2trash
4. Asks for your password **once** to install the polkit policy
   (enables journal, snap, apt-get, systemctl via pkexec — one password per session)
5. Creates the app launcher entry — app appears in Super key search immediately

**Step 3 — Launch:**
- Press **Super key** → type "CleanMint" → click the icon
- No terminal ever needed after this point

**To uninstall:**
```bash
bash ~/Cleanmint/install.sh --remove
# Then to also remove the polkit policy:
sudo rm /usr/share/polkit-1/actions/org.cleanmint.policy
```

---

### Manual run (development / testing without installing)
```bash
cd ~/Cleanmint
bash cleanmint/run.sh
```

---

## Architecture

### File layout
```
Cleanmint/
  venv/                          # Python virtualenv
  cleanmint/
    main.py                      # Entry point
    requirements.txt
    assets/
      org.cleanmint.policy       # Polkit policy XML (journalctl, snap, apt-get, systemctl)
    core/
      safety.py                  # Allowlist-based delete gate — ALL deletions go through here
      scanner.py                 # Read-only scan, returns ScanCategory list
      cleaner.py                 # Deletion engine, routes privileged cats to dedicated handlers
      analyzer.py                # Large files, folders, duplicates, broken symlinks
      health.py                  # 6 health checks, returns HealthCheck list
      startup.py                 # Startup app/service lister
      installer.py               # Polkit policy installer (pkexec tee)
      reporter.py                # PDF report export
    ui/
      main_window.py             # Sidebar nav, lazy-loaded pages, polkit setup dialog
      theme.py                   # Dark/light theme, stylesheet
      dashboard.py               # Dashboard page
      cleaner_page.py            # Cleaner page
      analyzer_page.py           # Analyzer page
      health_page.py             # Health page (includes AptUpgradeDialog)
      startup_page.py            # Startup page
      settings_page.py           # Settings page
      logs_page.py               # Logs page
    config/
      settings.py                # JSON settings at ~/.config/cleanmint/settings.json
  tests/
    test_safety.py               # 18 tests
    test_scanner.py              # 32 tests
    test_cleaner.py              # 19 tests
    test_browser_cache_fix.py    # 12 tests
```

---

## Rules (from idea.md)
- `subprocess`: NEVER use `shell=True`
- Deletions: ONLY through validated allowlists in `core/safety.py`
- UI: All scans/cleans must run in QThread — never block the main thread
- PEP8 compliance required throughout

## Protected Paths (never touch)
`/boot`, `/etc`, `/usr`, `/lib`, `/bin`, `/sbin`, `/dev`, `/proc`, `/sys`, `/root`

---

## Environment
- Python: 3.12.3
- venv: `/home/navdeep/Cleanmint/venv`
- Install: `venv/bin/pip install PyQt6 psutil reportlab send2trash`
- OS: Ubuntu (Linux 6.14.0-29-generic)

---

## Bugs Fixed — DO NOT REINTRODUCE

### 1. PyQt6: AA_UseHighDpiPixmaps is REMOVED
- `Qt.ApplicationAttribute.AA_UseHighDpiPixmaps` does not exist in PyQt6 — crashes on startup.
- High-DPI is automatic in PyQt6. Do NOT set this attribute. Remove the `app.setAttribute(...)` line.

### 2. Test helper functions must be defined BEFORE use
- In test files, helpers used in lambdas passed to `check()` must be defined before the first `check()` call.
- Python closures capture the name at call-time, not definition-time.

### 3. Privileged categories need dedicated handlers, not direct file deletion
- `snap` revisions and `journal` logs are root-owned — `shutil.rmtree` silently fails.
- `/tmp` has mixed ownership — only delete where `stat.st_uid == os.getuid()`.
- APT cache (`/var/cache/apt/archives/*.deb`) is root-owned — use `pkexec apt-get clean`.
- All three require polkit policy entries (`auth_admin_keep`) so password is cached per session.
- Handlers: `_clean_snap_revisions`, `_clean_journal`, `_clean_temp_files`, `_clean_apt_cache` in `cleaner.py`.
- UI must show skipped/error count after clean — not just "Done!".

### 4. Snap: "disabled" flag is a substring, not exact match
- `snap list --all` output has flags like `"disabled,classic"` or `"base,disabled"`, not just `"disabled"`.
- WRONG: `parts[5] == "disabled"` — misses most disabled snaps.
- CORRECT: `"disabled" in parts[5]` — fix applied in both `scanner.py` and `cleaner.py`.

### 5. APT cache: du exits non-zero due to /partial permission denied
- `du -sb /var/cache/apt/archives` fails with exit code 1 (permission on `/partial` subdir).
- WRONG: checking `returncode == 0` before reading size — always returns 0 MB.
- CORRECT: glob `*.deb` files directly and sum their `stat().st_size`.

### 6. Browser cache: target specific subdirs, NOT the whole profile folder
- Scanning `~/.cache/google-chrome/` whole would delete Cookies, Login Data, Bookmarks, History.
- CORRECT: enumerate profile subdirs and only collect dirs whose name is in the safe set:
  `Cache`, `cache2`, `Code Cache`, `GPUCache`, `ScriptCache`, `ShaderCache`, `Application Cache`, `Service Worker`.
- Sensitive data lives in `~/.config/google-chrome/` — never in allowlist.
- Test: `tests/test_browser_cache_fix.py` — 12 checks.

### 7. Cleaner: check parent paths BEFORE iterating directory children
- Passing a blocked dir (e.g. `/etc`) to `_collect_targets` without a parent check generates hundreds of warnings.
- Fix: check `is_blocked(path)` on each category path before iterating children.

### 8. Temp files scanner: only count user-owned files
- `_dir_size` on `/tmp` and `/var/tmp` counts ALL entries including root-owned `systemd-private-*` dirs.
- These dirs are actively used by running services and cannot be deleted — inflates reported size.
- CORRECT: in `_scan_temp_files`, filter by `stat.st_uid == os.getuid()` before counting.

### 9. Path.is_file() does not accept follow_symlinks parameter
- `Path.is_file(follow_symlinks=False)` raises `TypeError` — that parameter doesn't exist on Path methods.
- CORRECT: use `entry.is_symlink()` check separately, then plain `entry.is_file()` / `entry.is_dir()`.

### 10. Journal vacuum threshold must be aggressive enough to actually free space
- `--vacuum-size=200M` does nothing if the journal is already under 200 MB (e.g. 120 MB).
- CORRECT: use `--vacuum-size=50M --vacuum-time=7d` to actually reclaim space.
- Update dry-run estimate accordingly: `max(0, before_bytes - 50 * 1024 * 1024)`.

### 11. Flatpak: --dry-run flag does not exist
- `flatpak uninstall --unused --dry-run` → `error: Unknown option --dry-run`.
- CORRECT: pipe `"n\n"` as stdin to `flatpak uninstall --unused` for non-destructive detection.

### 12. Polkit policy needed for pkexec on Ubuntu 22.04+
- On Ubuntu 22.04+, pkexec requires a policy XML for each binary it authorises.
- Without the policy, pkexec calls for journalctl/snap/apt-get fail silently or with "Not authorized".
- Policy file: `cleanmint/assets/org.cleanmint.policy` — must be installed to `/usr/share/polkit-1/actions/`.
- App auto-installs it on first launch using `pkexec /usr/bin/tee` (works without pre-existing policy).
- Policy covers: `/usr/bin/journalctl`, `/usr/bin/snap`, `/usr/bin/apt-get`, `/usr/bin/systemctl`.
- All use `allow_active: auth_admin_keep` — password cached for the session.
- `installer.py` compares file content (not just existence) to detect outdated policy and prompt update.

### 13. Snap handler: do not use pkexec bash -c for batching
- `pkexec bash -c "snap remove ... && snap remove ..."` requires a policy for `bash`, not `snap`.
- CORRECT: call `pkexec /usr/bin/snap remove --revision REV NAME` per revision.
- With `auth_admin_keep` in policy, only the first call prompts — subsequent ones are automatic.

### 14. UI: description labels cause horizontal overflow
- Long description `QLabel`s in category rows expand to their full text width, pushing size labels off-screen.
- This affects: Cleaner page, Dashboard breakdown list.
- CORRECT:
  - Set `desc.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)` on description labels.
  - Give the size column a `setFixedWidth(90)` widget wrapper.
  - Set `scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)` on ALL scroll areas.
- Applied in: `cleaner_page.py`, `dashboard.py`, `health_page.py`, `settings_page.py`.

### 15. Disk % mismatch between Dashboard and Health page
- Dashboard used `int(used / total * 100)` (raw division).
- Health used `psutil.disk_usage().percent` which excludes root-reserved blocks — gives slightly different value.
- CORRECT: both use `round(usage.used / usage.total * 100, 1)` for consistency.

### 16. Health score counts "info" as failing
- Score was `sum(1 for c in checks if c.status == "ok")` — "info" items (e.g. Old Kernels kept as fallback) counted as failures.
- "info" means informational/non-critical — not a problem.
- CORRECT: `sum(1 for c in checks if c.status in ("ok", "info"))`.
- Issue counter: only `"warning"` and `"critical"` count as issues.

### 17. [DRY RUN] prefix leaking into UI status bar
- Cleaner logs prefix messages with `[DRY RUN]` internally; this was shown raw in the status label.
- CORRECT: strip it in `_on_clean_progress`: `msg.replace("[DRY RUN] ", "").replace("[DRY RUN]", "")`.

### 18. Analyzer: size column sorts alphabetically (string sort bug)
- `QTableWidget` sorts columns by display text — "9.6 GB" sorts before "974.6 MB" alphabetically.
- CORRECT: use `NumericTableWidgetItem` (subclass of `QTableWidgetItem`) that overrides `__lt__` to compare `Qt.ItemDataRole.UserRole` numeric data instead of display text.
- Apply to size, count, and wasted columns in all three populate methods.
