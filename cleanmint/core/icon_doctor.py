"""
core/icon_doctor.py — Icon Doctor: detects and fixes missing app icons

Scans .desktop files, resolves Icon= names against the system icon theme,
and auto-fixes missing icons for AppImage, Snap, and Flatpak apps.
"""

import os
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_DESKTOP_DIRS = [
    Path.home() / ".local/share/applications",
    Path("/usr/share/applications"),
]

_ICON_SEARCH_BASES = [
    Path.home() / ".local/share/icons",
    Path("/usr/share/icons"),
]

_ICON_EXTS = {".png", ".svg", ".xpm"}


@dataclass
class BrokenIconApp:
    name: str           # Display name from .desktop
    icon_name: str      # Icon= value
    desktop_file: str   # Full path to .desktop file
    exec_cmd: str       # Exec= value
    install_type: str   # "appimage" | "snap" | "flatpak" | "unknown"
    snap_name: str = "" # Snap package name (if snap)


# ── Icon resolution ────────────────────────────────────────────────────────────

def _all_icon_theme_dirs() -> list[Path]:
    """Return all icon theme base directories to search."""
    bases = [
        Path.home() / ".local/share/icons",
        Path("/usr/share/icons"),
    ]
    dirs = []
    for base in bases:
        if base.exists():
            dirs.append(base)
            # Include all subdirectories (each is a theme: hicolor, Adwaita, gnome, …)
            try:
                dirs.extend(p for p in base.iterdir() if p.is_dir())
            except OSError:
                pass
    return dirs


def _icon_is_installed(icon_name: str) -> bool:
    """Return True if the icon resolves to a real file on this system."""
    if not icon_name:
        return True

    p = Path(icon_name)
    if p.is_absolute():
        return p.exists()

    # Search all installed icon themes (hicolor, Adwaita, gnome, Humanity, …)
    for theme_dir in _all_icon_theme_dirs():
        for match in theme_dir.rglob(f"{icon_name}.*"):
            if match.suffix in _ICON_EXTS and match.is_file():
                return True

    # Pixmaps fallback
    for ext in _ICON_EXTS:
        if (Path("/usr/share/pixmaps") / f"{icon_name}{ext}").exists():
            return True

    return False


def _detect_install_type(exec_cmd: str) -> tuple[str, str]:
    """Return (install_type, snap_name) from the Exec= command."""
    import re
    lower = exec_cmd.lower()

    if ".appimage" in lower:
        return "appimage", ""

    if "/snap/" in lower or "snap run" in lower:
        m = re.search(r"/snap/([^/]+)/", exec_cmd)
        snap_name = m.group(1) if m else ""
        if not snap_name:
            m2 = re.search(r"snap run\s+(\S+)", exec_cmd)
            snap_name = m2.group(1).split(".")[0] if m2 else ""
        return "snap", snap_name

    if "flatpak" in lower:
        return "flatpak", ""

    return "unknown", ""


# ── Scanner ────────────────────────────────────────────────────────────────────

def scan_broken_icons() -> list[BrokenIconApp]:
    """Scan .desktop files and return apps whose icons are missing."""
    broken: list[BrokenIconApp] = []
    seen: set[str] = set()

    for desktop_dir in _DESKTOP_DIRS:
        if not desktop_dir.exists():
            continue
        for df in sorted(desktop_dir.glob("*.desktop")):
            try:
                content = df.read_text(errors="replace")
            except OSError:
                continue

            name = icon = exec_cmd = ""
            no_display = False

            for line in content.splitlines():
                if line.startswith("Name=") and not name:
                    name = line[5:].strip()
                elif line.startswith("Icon=") and not icon:
                    icon = line[5:].strip()
                elif line.startswith("Exec=") and not exec_cmd:
                    exec_cmd = line[5:].strip()
                elif line.strip() == "NoDisplay=true":
                    no_display = True

            if no_display or not name or not exec_cmd or not icon:
                continue
            if icon in seen:
                continue

            if not _icon_is_installed(icon):
                install_type, snap_name = _detect_install_type(exec_cmd)
                broken.append(BrokenIconApp(
                    name=name,
                    icon_name=icon,
                    desktop_file=str(df),
                    exec_cmd=exec_cmd,
                    install_type=install_type,
                    snap_name=snap_name,
                ))
                seen.add(icon)

    return broken


# ── Icon installation helper ───────────────────────────────────────────────────

def _install_icon(src: Path, icon_name: str) -> tuple[bool, str]:
    """Copy src to ~/.local/share/icons/hicolor and refresh caches."""
    if not src.exists() or not src.is_file():
        return False, f"Source not found: {src}"

    ext = src.suffix
    if ext not in {".png", ".svg"}:
        return False, f"Unsupported format: {ext}"

    size_dir = "512x512" if ext == ".png" else "scalable"
    dest_dir = Path.home() / ".local/share/icons/hicolor" / size_dir / "apps"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{icon_name}{ext}"
    shutil.copy2(str(src), str(dest))

    subprocess.run(
        ["gtk-update-icon-cache", "-f", "-t",
         str(Path.home() / ".local/share/icons/hicolor")],
        capture_output=True,
    )
    subprocess.run(
        ["update-desktop-database",
         str(Path.home() / ".local/share/applications")],
        capture_output=True,
    )
    return True, f"Installed to {dest}"


# ── Fix strategies ─────────────────────────────────────────────────────────────

def fix_icon(
    app: BrokenIconApp,
    progress: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """Fix the icon for app. Returns (success, message)."""
    log = progress or (lambda m: None)
    if app.install_type == "appimage":
        return _fix_appimage(app, log)
    if app.install_type == "snap":
        return _fix_snap(app, log)
    if app.install_type == "flatpak":
        return _fix_flatpak(app, log)
    return False, "Cannot auto-fix: unknown install type"


def _fix_appimage(app: BrokenIconApp, log: Callable) -> tuple[bool, str]:
    try:
        parts = shlex.split(app.exec_cmd)
    except ValueError:
        parts = app.exec_cmd.split()

    appimage_path: str | None = None
    for part in parts:
        if part.lower().endswith(".appimage") and Path(part).exists():
            appimage_path = part
            break

    if not appimage_path:
        return False, "AppImage file not found on disk"

    log(f"Extracting icon from {Path(appimage_path).name}…")

    icon_name = app.icon_name
    candidate_patterns = [
        f"usr/share/icons/hicolor/512x512/apps/{icon_name}.png",
        f"usr/share/icons/hicolor/256x256/apps/{icon_name}.png",
        f"usr/share/icons/hicolor/128x128/apps/{icon_name}.png",
        f"usr/share/pixmaps/{icon_name}.png",
        f"usr/share/icons/hicolor/scalable/apps/{icon_name}.svg",
        f"{icon_name}.png",  # root-level (often a symlink — we resolve it)
    ]

    old_cwd = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="cleanmint_icon_") as tmpdir:
        try:
            os.chdir(tmpdir)
            squash = Path(tmpdir) / "squashfs-root"

            for pattern in candidate_patterns:
                subprocess.run(
                    [appimage_path, "--appimage-extract", pattern],
                    capture_output=True, timeout=60,
                )
                candidate = squash / pattern

                if candidate.is_symlink():
                    # Symlink target is relative to squashfs-root
                    link_target = os.readlink(str(candidate))
                    if not Path(link_target).is_absolute():
                        resolved = (candidate.parent / link_target).resolve()
                        try:
                            real_pattern = str(resolved.relative_to(squash))
                        except ValueError:
                            continue
                    else:
                        real_pattern = link_target.lstrip("/")

                    subprocess.run(
                        [appimage_path, "--appimage-extract", real_pattern],
                        capture_output=True, timeout=60,
                    )
                    real_file = squash / real_pattern
                    if real_file.is_file() and not real_file.is_symlink():
                        log(f"Found: {real_file.name}")
                        return _install_icon(real_file, icon_name)

                elif candidate.is_file():
                    log(f"Found: {candidate.name}")
                    return _install_icon(candidate, icon_name)

            # Last resort: full extraction
            log("Doing full extraction to find icon…")
            subprocess.run(
                [appimage_path, "--appimage-extract"],
                capture_output=True, timeout=300,
            )
            for ext in (".png", ".svg"):
                matches = [
                    m for m in squash.rglob(f"*{icon_name}*{ext}")
                    if m.is_file() and not m.is_symlink()
                ]
                if matches:
                    matches.sort(key=lambda x: x.stat().st_size, reverse=True)
                    log(f"Found: {matches[0].name}")
                    return _install_icon(matches[0], icon_name)

        except subprocess.TimeoutExpired:
            return False, "AppImage extraction timed out"
        except Exception as e:
            return False, f"Extraction error: {e}"
        finally:
            os.chdir(old_cwd)

    return False, "Could not find icon inside AppImage"


def _fix_snap(app: BrokenIconApp, log: Callable) -> tuple[bool, str]:
    snap_name = app.snap_name or app.icon_name
    log(f"Looking for snap icon: {snap_name}…")

    search_bases = [
        Path("/snap") / snap_name / "current" / "meta" / "gui",
        Path("/snap") / snap_name / "current" / "usr" / "share" / "icons",
        Path("/snap") / snap_name / "current" / "usr" / "share" / "pixmaps",
    ]

    for base in search_bases:
        if not base.exists():
            continue
        for ext in (".png", ".svg"):
            for candidate in [base / f"{app.icon_name}{ext}", base / f"icon{ext}"]:
                if candidate.is_file():
                    return _install_icon(candidate, app.icon_name)
        for match in base.rglob(f"*{app.icon_name}*"):
            if match.is_file() and match.suffix in _ICON_EXTS:
                return _install_icon(match, app.icon_name)

    return False, f"Icon not found in snap dirs for '{snap_name}'"


def _fix_flatpak(app: BrokenIconApp, log: Callable) -> tuple[bool, str]:
    log(f"Looking for flatpak icon: {app.icon_name}…")

    icon_name = app.icon_name
    bases = [
        Path.home() / ".local/share/flatpak/exports/share/icons",
        Path("/var/lib/flatpak/exports/share/icons"),
    ]
    sizes = ["512x512", "256x256", "128x128", "64x64", "scalable"]

    for base in bases:
        if not base.exists():
            continue
        # Direct lookup first (fast — no rglob)
        for size in sizes:
            ext = ".svg" if size == "scalable" else ".png"
            candidate = base / "hicolor" / size / "apps" / f"{icon_name}{ext}"
            if candidate.is_file():
                log(f"Found: {candidate.name}")
                return _install_icon(candidate, icon_name)

    # Check if flatpak is even installed for this app
    try:
        r = subprocess.run(
            ["flatpak", "info", icon_name],
            capture_output=True, timeout=5,
        )
        if r.returncode != 0:
            return False, "App is not installed via Flatpak (stale .desktop file?)"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return False, "Icon not found in Flatpak exports"
