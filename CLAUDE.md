# CLAUDE.md — CleanMint Project Notes

## Project
CleanMint: A commercial-grade Linux system cleaner for Ubuntu, built with Python 3.12 + PyQt6.
GitHub: https://github.com/sonynavdeep81/CleanMint

---

## Current Working State (as of 2026-04-13)

### What is fully working
- **Cleaner page**: Scans 10 categories, shows sizes, checkbox selection, preview dialog, real clean
- **Analyzer page**: Large files, large folders, duplicate detection (hash + name+size), broken symlinks
  - **Delete broken symlinks**: "Remove All" button — deletes dead shortcuts, never actual files
  - **Delete duplicate copies**: "Delete Copies" button — only deletes copies found in `.cache` or `/tmp`;
    copies in Downloads and personal folders (Documents, Pictures, etc.) are NEVER auto-deleted;
    copies in personal folders are always kept; `validate_delete()` runs on every file before deletion
- **Dashboard**: Disk usage bar, junk summary, health score, junk breakdown list, navigate-to-cleaner button
- **Health page**: 6 checks (disk, broken packages, failed services, old kernels, journal, pending updates)
  - "Run apt upgrade" opens a streaming live-output dialog (no terminal needed)
  - "Show Status" + "Restart Services" buttons on failed services row using pkexec helper
- **Startup page**: Lists XDG autostart apps and systemd user services
  - Safety ratings per entry: Keep (green) / Safe (blue) / Caution (orange) / Unknown (grey)
  - Click any row for a plain-English explanation of what the service does and whether it is safe to disable
- **Snapshot page**: Captures installed packages (apt, snap, flatpak) + PPA sources into a timestamped snapshot
  - "Take Snapshot" runs in background with live progress bar; prompts for optional label
  - "Export Restore Script" saves a `restore.sh` — run it on any fresh Ubuntu install to recreate the environment
  - "Compare Two" shows a `+`/`-` diff of packages added/removed between any two snapshots
  - Snapshots stored in `~/.local/share/cleanmint/snapshots/` (never committed to git)
  - Restore script handles PPAs, apt, snap, flatpak in order; uses `|| true` so one failure never aborts the rest
- **Printer Profile page**: Lists all configured CUPS printers with status details; exports a `restore_printers.sh` script to recreate printer configs on a new machine
- **VS Code Profile page**: Shows all installed VS Code extensions (name, ID, version) + user `settings.json`; exports a `restore_vscode.sh` to reinstall extensions on any machine
- **Settings page**: Dark/light mode, persistent JSON settings
- **Logs page**: Shows CleanMint session logs with color-coded highlighting
  - `[DELETED]` / `[SNAP REMOVED]` lines highlighted red — shows exactly what was removed
  - `[SNAP PROTECTED]` / `BLOCKED` highlighted orange — skipped for safety
  - `[DRY-RUN]` highlighted grey — preview only, nothing deleted
  - **"Deletions Only" filter button** — one click shows only actual deletions across the session
- **Polkit policy**: Installed at `/usr/share/polkit-1/actions/org.cleanmint.policy`
  - Single helper script `/usr/local/lib/cleanmint/cleanmint-helper` covers all privileged ops
  - Covers: journalctl, snap, apt-get, systemctl — all with `auth_admin_keep` (one password per session)
  - App detects if policy is missing/outdated and offers to install/update it on launch
- **Custom app icon**: Mint-green gear on dark rounded square, generated as SVG + PNG at 7 sizes
  - Installed to `~/.local/share/icons/hicolor/` — appears in app launcher immediately
- **Tests**: 81/81 passing across 4 test suites

---

## Installation & Running

### On this machine (already set up)
CleanMint is installed and ready. Launch it by:
- Pressing the **Super key**, typing "CleanMint", clicking the icon, OR
- Running `cleanmint` in a terminal

If the icon is missing, re-run the full desktop install:
```bash
bash ~/Cleanmint/install.sh
```

---

### On a new machine — full install from scratch

**Prerequisites:** Ubuntu 22.04+ (or any Debian-based distro with Python 3.10+)

**Step 1 — Get the files (pick one method):**

Option A — GitHub (recommended):
```bash
git clone https://github.com/sonynavdeep81/CleanMint.git ~/Cleanmint
```

Option B — USB / zip:
```bash
# On this machine:
cd ~ && zip -r cleanmint.zip Cleanmint --exclude 'Cleanmint/venv/*' --exclude '*/__pycache__/*'
# Copy zip to USB, then on new machine:
unzip cleanmint.zip -d ~
```

Option C — Network (SSH):
```bash
scp -r ~/Cleanmint user@NEW_MACHINE_IP:~/Cleanmint
```

**Step 2 — Run the installer:**
```bash
cd ~/Cleanmint
bash install.sh
```

The installer automatically:
1. Checks Python 3.10+ is available
2. Creates a Python virtual environment
3. Installs all dependencies: PyQt6, psutil, reportlab, send2trash
4. Installs the polkit policy (asks for your password **once**)
   — enables journal vacuum, snap cleanup, apt cache clean, service restart via pkexec
5. Installs the custom app icon at all sizes (16 → 512 px)
6. Creates the desktop launcher entry — app appears in Super key search immediately

**Step 3 — Launch:**
- Press **Super key** → type "CleanMint" → click the icon
- No terminal ever needed after this point

**To uninstall:**
```bash
bash ~/Cleanmint/install.sh --remove
sudo rm /usr/share/polkit-1/actions/org.cleanmint.policy
sudo rm /usr/local/lib/cleanmint/cleanmint-helper
```

---

### Restoring your apps on a new machine using a Snapshot

If you previously took a snapshot on your old machine, you can restore all your
installed apps (apt, snap, flatpak) onto any fresh Ubuntu install — **no CleanMint
needed on the new machine**.

**Step 1 — Export the restore script (on your old machine):**
- Open CleanMint → Snapshots → select your snapshot → "⬇ Export Restore Script"
- Save `restore.sh` to a USB drive, cloud storage, or email it to yourself

**Step 2 — On the new machine (after fresh Ubuntu install):**
```bash
bash restore.sh
```

The script will automatically:
1. Add your PPAs
2. Install all apt packages
3. Install Snap apps
4. Install Flatpak apps

If any single package fails (e.g. removed from repos), it is skipped with `|| true`
and the rest continue — the restore never aborts mid-way.

**Step 3 — Reboot**

> Note: The restore script installs your *software environment* only.
> Personal files (Documents, Pictures, code) must be copied separately via USB or rsync.

**Complete new-machine workflow (CleanMint + all your apps):**
```bash
# 1. Install CleanMint
git clone https://github.com/sonynavdeep81/CleanMint.git ~/Cleanmint
cd ~/Cleanmint && bash install.sh

# 2. Restore all your apps
bash restore.sh
```

---

### Restoring VS Code extensions on a new machine

**Step 1 — Export (on your old machine):**
- Open CleanMint → VS Code Profile → "⬇ Export Restore Script"
- Save `restore_vscode.sh` to USB / cloud

**Step 2 — On the new machine:**
```bash
bash restore_vscode.sh
```
Installs all extensions via `code --install-extension`. Requires VS Code to already be installed.

---

### Restoring printers on a new machine

**Step 1 — Export (on your old machine):**
- Open CleanMint → Printer Profile → "⬇ Export Restore Script"
- Save `restore_printers.sh` to USB / cloud

**Step 2 — On the new machine:**
```bash
bash restore_printers.sh
```
Re-adds each printer via `lpadmin`. Requires CUPS to be running (`sudo systemctl start cups`).

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
  install.sh                       # One-command installer for any machine
  venv/                            # Python virtualenv (not committed)
  cleanmint/
    main.py                        # Entry point
    requirements.txt
    run.sh                         # Dev launcher
    assets/
      org.cleanmint.policy         # Polkit policy XML
      cleanmint-helper             # Privileged helper script (bash)
      cleanmint.desktop            # Desktop entry template
      icons/
        cleanmint.svg              # Master SVG icon (mint gear on dark bg)
        cleanmint_16.png  … cleanmint_512.png   # Pre-rendered PNG sizes
    core/
      safety.py                    # Allowlist-based delete gate — ALL deletions go through here
      scanner.py                   # Read-only scan, returns ScanCategory list
      cleaner.py                   # Deletion engine, routes privileged cats to dedicated handlers
      analyzer.py                  # Large files, folders, duplicates, broken symlinks
      health.py                    # 6 health checks, returns HealthCheck list
      startup.py                   # Startup app/service lister + safety knowledge base
      snapshot.py                  # Snapshot engine: capture packages, diff, generate restore.sh
      printer.py                   # CUPS printer lister + restore script generator
      vscode.py                    # VS Code extension/settings reader + restore script generator
      installer.py                 # Polkit policy + helper installer (pkexec tee)
      reporter.py                  # PDF report export
    ui/
      main_window.py               # Sidebar nav, lazy-loaded pages, polkit setup dialog
      theme.py                     # Dark/light theme, stylesheet (includes DangerBtn)
      dashboard.py                 # Dashboard page
      cleaner_page.py              # Cleaner page
      analyzer_page.py             # Analyzer page (delete duplicates + broken symlinks)
      health_page.py               # Health page (AptUpgradeDialog, service restart)
      startup_page.py              # Startup page (safety badges, detail popup)
      snapshot_page.py             # Snapshot page (take, export, compare, delete)
      printer_page.py              # Printer Profile page (CUPS printer list, export restore script)
      vscode_page.py               # VS Code Profile page (extensions viewer, export restore script)
      settings_page.py             # Settings page
      logs_page.py                 # Logs page
    config/
      settings.py                  # JSON settings at ~/.config/cleanmint/settings.json
  tests/
    test_safety.py                 # 18 tests
    test_scanner.py                # 32 tests
    test_cleaner.py                # 19 tests
    test_browser_cache_fix.py      # 12 tests
    test_ui_imports.py
    test_backend_phase5.py
    test_integration.py
```

---

## Rules
- `subprocess`: NEVER use `shell=True`
- Deletions: ONLY through validated allowlists in `core/safety.py`
- UI: All scans/cleans must run in QThread — never block the main thread
- PEP8 compliance required throughout
- Downloads folder: NEVER auto-delete anything from `~/Downloads`

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
- High-DPI is automatic in PyQt6. Do NOT set this attribute.

### 2. Test helper functions must be defined BEFORE use
- Python closures capture the name at call-time, not definition-time.

### 3. Privileged categories need dedicated handlers, not direct file deletion
- `snap` revisions and `journal` logs are root-owned — `shutil.rmtree` silently fails.
- `/tmp` has mixed ownership — only delete where `stat.st_uid == os.getuid()`.
- APT cache is root-owned — use `pkexec apt-get clean`.
- Handlers: `_clean_snap_revisions`, `_clean_journal`, `_clean_temp_files`, `_clean_apt_cache` in `cleaner.py`.

### 4. Snap: "disabled" flag is a substring, not exact match
- WRONG: `parts[5] == "disabled"` — CORRECT: `"disabled" in parts[5]`

### 5. APT cache: du exits non-zero due to /partial permission denied
- CORRECT: glob `*.deb` files directly and sum their `stat().st_size`.

### 6. Browser cache: target specific subdirs, NOT the whole profile folder
- Sensitive data lives in `~/.config/google-chrome/` — never in allowlist.
- Only collect dirs whose name is in the safe set: Cache, cache2, Code Cache, GPUCache, etc.

### 7. Cleaner: check parent paths BEFORE iterating directory children
- Fix: check `is_blocked(path)` on each category path before iterating children.

### 8. Temp files scanner: only count user-owned files
- Filter by `stat.st_uid == os.getuid()` before counting.

### 9. Path.is_file() does not accept follow_symlinks parameter
- CORRECT: use `entry.is_symlink()` check separately, then plain `entry.is_file()` / `entry.is_dir()`.

### 10. Journal vacuum threshold must be aggressive enough to actually free space
- CORRECT: use `--vacuum-size=50M --vacuum-time=7d`.

### 11. Flatpak: --dry-run flag does not exist
- CORRECT: pipe `"n\n"` as stdin to `flatpak uninstall --unused`.

### 12. Polkit policy needed for pkexec on Ubuntu 22.04+
- Policy file: `cleanmint/assets/org.cleanmint.policy` — must be installed to `/usr/share/polkit-1/actions/`.
- App auto-installs it on first launch using `pkexec /usr/bin/tee`.

### 13. Snap handler: do not use pkexec bash -c for batching
- CORRECT: call `pkexec HELPER snap-remove REV NAME` per revision via the helper script.

### 14. UI: description labels cause horizontal overflow
- CORRECT: `setSizePolicy(Ignored, Preferred)` + `ScrollBarAlwaysOff` + fixed-width right panel.

### 15. Disk % mismatch between Dashboard and Health page
- CORRECT: both use `round(usage.used / usage.total * 100, 1)`.

### 16. Health score counts "info" as failing
- CORRECT: `sum(1 for c in checks if c.status in ("ok", "info"))`.

### 17. [DRY RUN] prefix leaking into UI status bar
- CORRECT: strip it in `_on_clean_progress`.

### 18. Analyzer: size column sorts alphabetically (string sort bug)
- CORRECT: use `NumericTableWidgetItem` that overrides `__lt__` to compare `UserRole` numeric data.

### 19. Multiple password prompts for different privileged operations
- WRONG: separate pkexec calls for different binaries = separate polkit actions = multiple prompts.
- CORRECT: single helper script `/usr/local/lib/cleanmint/cleanmint-helper` with one polkit action
  (`org.cleanmint.helper`) using `auth_admin_keep` — one password covers all operations per session.

### 20. Duplicate deletion: never delete from Downloads or personal folders
- WRONG: keeping "alphabetically first" copy could delete files from Documents/Pictures.
- CORRECT: `_SAFE_ZONES` = only `.cache` and `/tmp`/`/var/tmp`. Downloads is explicitly excluded.
  Only delete a copy when at least one copy exists OUTSIDE the safe zone (so data is always preserved).
  All deletions still go through `validate_delete()`.

### 21. Snap revision cleaner must never remove GPU/platform content snaps
- WRONG: cleaning all "disabled" snap revisions can remove `mesa-2404` (GPU driver) or GNOME platform
  snaps — this silently breaks apps like the Ubuntu App Center (snap-store) without any obvious error.
- CORRECT: `_PROTECTED_SNAPS` in `cleaner.py` lists snaps that are NEVER removed even if disabled:
  `mesa-2404`, `mesa-2204`, `core`/`core18`/`core20`/`core22`/`core24`, all `gnome-*` platform snaps,
  `gtk-common-themes`, `snapd`. Protected snaps log as `[SNAP PROTECTED]` and are skipped silently.
