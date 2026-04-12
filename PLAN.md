# CleanMint — Implementation Plan

## Feasibility: YES
- Python 3.12 + PyQt6 covers everything in the spec
- All cleanup ops use standard subprocess calls (apt, snap, flatpak, journalctl, etc.)
- Safety layer via allowlists is straightforward
- Estimated total code: ~4000–6000 lines across all modules

## Complexity: HIGH
- 7 distinct UI sections with real data binding
- Non-blocking UI requires QThread workers for every scan/clean op
- Subprocess safety (no shell=True) adds verbosity but is non-negotiable
- Report export (PDF) needs reportlab or weasyprint
- Packaging to .deb / AppImage is a separate post-build step

---

## Phases

### Phase 1 — Project Scaffold & Safety Core
- Create full folder structure
- `requirements.txt`
- `core/safety.py` — path allowlists, blocklists, confirmation guards
- `config/settings.py` — persistent JSON settings
- `main.py` entry point

### Phase 2 — Core Backend Modules
- `core/scanner.py` — threaded disk scan, space estimates per category
- `core/cleaner.py` — safe delete, dry-run mode, action log
- `core/analyzer.py` — large files/folders, file type breakdown
- `core/health.py` — broken packages, failed services, disk risk, kernel count
- `core/startup.py` — autostart apps (XDG + systemd user services)

### Phase 3 — Main Window & Dashboard UI
- `ui/main_window.py` — sidebar nav, stacked pages
- `ui/dashboard.py` — disk usage widget, health score, junk estimate, last clean date
- Theme engine — dark/light mode toggle, consistent palette

### Phase 4 — Cleaner UI
- `ui/cleaner.py` — category list with checkboxes, risk badges, size estimates
- One-click "Free Space Safely" button
- Dry-run preview dialog before actual deletion
- Progress bar with live feedback via QThread signals

### Phase 5 — Advanced Cleanup & Analyzer UI
- `ui/advanced.py` — snap revisions, flatpak, old kernels, duplicates, broken symlinks
- `ui/analyzer.py` — large file list (sortable), folder tree, quick-open button

### Phase 6 — Startup & Health UI
- `ui/startup.py` — table of autostart entries, enable/disable toggle
- `ui/health.py` — checklist-style health report with fix buttons

### Phase 7 — Settings, Logs & Reports
- `ui/settings.py` — exclusions, scan-on-start, dark mode, reminders
- `ui/logs.py` — filterable log viewer
- `core/reporter.py` — export to txt / csv / pdf

### Phase 8 — Polish & Packaging
- Icons, consistent spacing, tooltips
- `.deb` packaging with `dpkg-deb`
- AppImage with `appimagetool`
- README / installation instructions

---

## File Structure

```
cleanmint/
├── main.py
├── requirements.txt
├── config/
│   ├── __init__.py
│   └── settings.py
├── core/
│   ├── __init__.py
│   ├── safety.py
│   ├── scanner.py
│   ├── cleaner.py
│   ├── analyzer.py
│   ├── health.py
│   ├── startup.py
│   └── reporter.py
├── ui/
│   ├── __init__.py
│   ├── main_window.py
│   ├── dashboard.py
│   ├── cleaner.py
│   ├── advanced.py
│   ├── analyzer.py
│   ├── startup.py
│   ├── health.py
│   ├── settings.py
│   ├── logs.py
│   └── theme.py
├── assets/
│   └── icons/
└── logs/
```

---

## Key Technical Decisions

| Decision | Choice | Reason |
|---|---|---|
| GUI framework | PyQt6 | Mature, feature-rich, native look |
| Threading | QThread + signals | Non-blocking UI, PyQt-native |
| Settings | JSON via pathlib | Simple, no DB dependency |
| PDF export | reportlab | Pure Python, no system deps |
| Safety | Allowlist-only deletes | Never blocklist-based |
| subprocess | list args only | No shell=True, prevents injection |

---

## Questions Before Starting
1. Should the app require `sudo` for some ops (apt clean, kernel removal) or ask for password at runtime via `pkexec`?
2. Any preferred color theme / branding direction?
3. Should duplicate file detection use hashing (accurate but slow) or size+name (fast)?
4. Target .deb or AppImage as primary distribution format?
