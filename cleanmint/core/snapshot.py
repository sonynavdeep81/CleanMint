"""
core/snapshot.py — System Snapshot Engine

Captures installed packages (apt, snap, flatpak), PPA sources, and generates
a portable bash restore script that can be run on any fresh Ubuntu install.
"""

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SNAPSHOT_DIR = Path.home() / ".local" / "share" / "cleanmint" / "snapshots"


@dataclass
class SnapshotMeta:
    name: str           # directory slug (timestamp-based)
    label: str          # user-visible label
    created_at: str     # ISO timestamp string
    hostname: str
    distro: str
    apt_count: int
    snap_count: int
    flatpak_count: int
    path: Path


class SnapshotEngine:
    """Takes, lists, deletes snapshots and generates restore scripts."""

    # ── Public API ─────────────────────────────────────────────────────────

    def take(self, label: str = "", progress_callback=None) -> SnapshotMeta:
        """Capture the current package state and persist it to disk."""
        def _prog(msg: str, pct: int):
            if progress_callback:
                progress_callback(msg, pct)

        now = datetime.now()
        slug = now.strftime("%Y%m%d_%H%M%S")
        snap_path = self._ensure_dir() / slug
        snap_path.mkdir(parents=True, exist_ok=True)

        _prog("Collecting apt packages…", 10)
        apt = self._get_apt_packages()

        _prog("Collecting Snap packages…", 30)
        snaps = self._get_snap_packages()

        _prog("Collecting Flatpak packages…", 50)
        flatpaks = self._get_flatpak_packages()

        _prog("Collecting PPA sources…", 65)
        ppas = self._get_ppa_sources()

        _prog("Saving snapshot data…", 80)
        (snap_path / "apt_packages.txt").write_text("\n".join(apt))
        (snap_path / "snap_packages.txt").write_text("\n".join(snaps))
        (snap_path / "flatpak_packages.txt").write_text("\n".join(flatpaks))
        (snap_path / "ppa_sources.txt").write_text("\n".join(ppas))

        resolved_label = label.strip() or now.strftime("Snapshot %d %b %Y %H:%M")
        meta_dict = {
            "label":         resolved_label,
            "created_at":    now.isoformat(),
            "hostname":      self._hostname(),
            "distro":        self._distro(),
            "apt_count":     len(apt),
            "snap_count":    len(snaps),
            "flatpak_count": len(flatpaks),
        }
        (snap_path / "manifest.json").write_text(json.dumps(meta_dict, indent=2))

        _prog("Generating restore script…", 92)
        script = self._build_restore_script(meta_dict, apt, snaps, flatpaks, ppas)
        (snap_path / "restore.sh").write_text(script)
        os.chmod(snap_path / "restore.sh", 0o755)

        _prog("Done.", 100)

        return SnapshotMeta(
            name=slug,
            label=resolved_label,
            created_at=meta_dict["created_at"],
            hostname=meta_dict["hostname"],
            distro=meta_dict["distro"],
            apt_count=len(apt),
            snap_count=len(snaps),
            flatpak_count=len(flatpaks),
            path=snap_path,
        )

    def list_snapshots(self) -> list[SnapshotMeta]:
        """Return all saved snapshots, newest first."""
        results: list[SnapshotMeta] = []
        if not SNAPSHOT_DIR.exists():
            return results
        for entry in sorted(SNAPSHOT_DIR.iterdir(), reverse=True):
            manifest = entry / "manifest.json"
            if not manifest.is_file():
                continue
            try:
                m = json.loads(manifest.read_text())
                results.append(SnapshotMeta(
                    name=entry.name,
                    label=m.get("label", entry.name),
                    created_at=m.get("created_at", ""),
                    hostname=m.get("hostname", ""),
                    distro=m.get("distro", ""),
                    apt_count=m.get("apt_count", 0),
                    snap_count=m.get("snap_count", 0),
                    flatpak_count=m.get("flatpak_count", 0),
                    path=entry,
                ))
            except Exception:
                continue
        return results

    def delete(self, name: str) -> None:
        """Delete a snapshot directory by slug name."""
        target = SNAPSHOT_DIR / name
        if target.exists() and target.parent == SNAPSHOT_DIR:
            shutil.rmtree(target)

    def export_restore_script(self, name: str, dest: Path) -> Path:
        """Copy restore.sh from a snapshot to an arbitrary destination."""
        src = SNAPSHOT_DIR / name / "restore.sh"
        shutil.copy2(src, dest)
        os.chmod(dest, 0o755)
        return dest

    def diff(self, name_a: str, name_b: str) -> dict:
        """Return added/removed packages between two snapshots (A → B)."""
        def _load(slug: str, fname: str) -> set[str]:
            p = SNAPSHOT_DIR / slug / fname
            return set(p.read_text().splitlines()) if p.is_file() else set()

        result: dict = {}
        for category, fname in [
            ("apt",     "apt_packages.txt"),
            ("snap",    "snap_packages.txt"),
            ("flatpak", "flatpak_packages.txt"),
        ]:
            a = _load(name_a, fname)
            b = _load(name_b, fname)
            result[category] = {
                "added":   sorted(b - a),
                "removed": sorted(a - b),
            }
        return result

    # ── Package collectors ─────────────────────────────────────────────────

    def _get_apt_packages(self) -> list[str]:
        try:
            out = subprocess.run(
                ["dpkg", "--get-selections"],
                capture_output=True, text=True, timeout=30,
            )
            pkgs = []
            for line in out.stdout.splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == "install":
                    pkgs.append(parts[0])
            return sorted(pkgs)
        except Exception:
            return []

    def _get_snap_packages(self) -> list[str]:
        if not shutil.which("snap"):
            return []
        try:
            out = subprocess.run(
                ["snap", "list"],
                capture_output=True, text=True, timeout=15,
            )
            pkgs = []
            for line in out.stdout.splitlines()[1:]:  # skip header row
                parts = line.split()
                if parts:
                    pkgs.append(parts[0])
            return sorted(pkgs)
        except Exception:
            return []

    def _get_flatpak_packages(self) -> list[str]:
        if not shutil.which("flatpak"):
            return []
        try:
            out = subprocess.run(
                ["flatpak", "list", "--app", "--columns=application"],
                capture_output=True, text=True, timeout=15,
            )
            return sorted(
                line.strip()
                for line in out.stdout.splitlines()
                if line.strip()
            )
        except Exception:
            return []

    def _get_ppa_sources(self) -> list[str]:
        sources: list[str] = []
        sources_dir = Path("/etc/apt/sources.list.d")
        if not sources_dir.exists():
            return sources
        for f in sorted(sources_dir.iterdir()):
            if f.suffix not in (".list", ".sources") or not f.is_file():
                continue
            try:
                for line in f.read_text().splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "ppa.launchpad.net" not in line:
                        continue
                    for part in line.split():
                        if "ppa.launchpad.net" not in part:
                            continue
                        segs = part.rstrip("/").split("/")
                        try:
                            idx = segs.index("ppa.launchpad.net")
                            ppa = f"ppa:{segs[idx + 1]}/{segs[idx + 2]}"
                            if ppa not in sources:
                                sources.append(ppa)
                        except (ValueError, IndexError):
                            pass
            except Exception:
                pass
        return sources

    # ── System info ────────────────────────────────────────────────────────

    def _hostname(self) -> str:
        try:
            return subprocess.run(
                ["hostname"], capture_output=True, text=True
            ).stdout.strip()
        except Exception:
            return "unknown"

    def _distro(self) -> str:
        try:
            return subprocess.run(
                ["lsb_release", "-ds"], capture_output=True, text=True
            ).stdout.strip().strip('"')
        except Exception:
            return "Linux"

    # ── Restore script builder ─────────────────────────────────────────────

    def _build_restore_script(
        self,
        meta: dict,
        apt: list[str],
        snaps: list[str],
        flatpaks: list[str],
        ppas: list[str],
    ) -> str:
        created  = meta.get("created_at", "")[:10]
        label    = meta.get("label", "Snapshot")
        distro   = meta.get("distro", "Linux")
        hostname = meta.get("hostname", "unknown")

        lines = [
            "#!/usr/bin/env bash",
            "# ─────────────────────────────────────────────────────────────────────",
            "#  CleanMint Restore Script",
            f"#  Snapshot : {label}",
            f"#  Created  : {created}",
            f"#  Source   : {hostname} ({distro})",
            "# ─────────────────────────────────────────────────────────────────────",
            "#  Run on a FRESH Ubuntu install to restore your software environment.",
            "#  Your personal files are NOT included — restore them separately.",
            "# ─────────────────────────────────────────────────────────────────────",
            "",
            "set -euo pipefail",
            "",
            'echo "CleanMint restore starting…"',
            "",
        ]

        if ppas:
            lines += [
                "# ── Add PPAs ──────────────────────────────────────────────────────────",
                "echo 'Adding PPAs…'",
            ]
            for ppa in ppas:
                lines.append(f"sudo add-apt-repository -y '{ppa}' || true")
            lines += ["sudo apt-get update -y", ""]

        if apt:
            lines += [
                "# ── Install apt packages ──────────────────────────────────────────────",
                "echo 'Installing apt packages…'",
                "sudo apt-get update -y",
                "sudo apt-get install -y --no-install-recommends \\",
            ]
            chunks = [apt[i:i + 6] for i in range(0, len(apt), 6)]
            for i, chunk in enumerate(chunks):
                suffix = " \\" if i < len(chunks) - 1 else ""
                lines.append("  " + " ".join(f"'{p}'" for p in chunk) + suffix)
            lines.append("")

        if snaps:
            lines += [
                "# ── Install Snap packages ─────────────────────────────────────────────",
                "echo 'Installing Snap packages…'",
            ]
            for snap in snaps:
                lines.append(f"sudo snap install '{snap}' || true")
            lines.append("")

        if flatpaks:
            lines += [
                "# ── Install Flatpak packages ──────────────────────────────────────────",
                "echo 'Installing Flatpak packages…'",
                "flatpak remote-add --if-not-exists flathub "
                "https://dl.flathub.org/repo/flathub.flatpakrepo || true",
            ]
            for fp in flatpaks:
                lines.append(f"flatpak install -y flathub '{fp}' || true")
            lines.append("")

        lines += [
            'echo ""',
            'echo "✓  Restore complete. Reboot recommended."',
        ]

        return "\n".join(lines) + "\n"

    # ── Internal ───────────────────────────────────────────────────────────

    @staticmethod
    def _ensure_dir() -> Path:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        return SNAPSHOT_DIR
