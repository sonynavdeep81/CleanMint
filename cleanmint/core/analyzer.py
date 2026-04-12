"""
core/analyzer.py — CleanMint Large File & Folder Analyzer

Read-only. Scans for largest files/folders, file type breakdown,
duplicate detection (hash or name+size), and broken symlinks.
Never deletes anything.
"""

import os
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable


HOME = Path.home()

# Paths to skip entirely during deep scans
SKIP_ROOTS = frozenset([
    "/proc", "/sys", "/dev", "/run",
    "/boot", "/snap/core",
])


@dataclass
class FileEntry:
    path: Path
    size: int
    modified: datetime
    file_type: str      # e.g. "Video", "Image", "Archive", …

    @property
    def size_human(self) -> str:
        return _human_size(self.size)

    @property
    def modified_str(self) -> str:
        return self.modified.strftime("%Y-%m-%d")


@dataclass
class FolderEntry:
    path: Path
    size: int
    file_count: int

    @property
    def size_human(self) -> str:
        return _human_size(self.size)


@dataclass
class DuplicateGroup:
    key: str            # hash or "name::size"
    files: list[Path] = field(default_factory=list)
    size: int = 0       # size of ONE copy

    @property
    def wasted(self) -> int:
        return self.size * (len(self.files) - 1)

    @property
    def wasted_human(self) -> str:
        return _human_size(self.wasted)


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# Extension → human-readable type
_EXT_TYPES: dict[str, str] = {
    # Video
    ".mp4": "Video", ".mkv": "Video", ".avi": "Video", ".mov": "Video",
    ".wmv": "Video", ".flv": "Video", ".webm": "Video",
    # Image
    ".jpg": "Image", ".jpeg": "Image", ".png": "Image", ".gif": "Image",
    ".bmp": "Image", ".svg": "Image", ".webp": "Image", ".heic": "Image",
    ".tiff": "Image",
    # Audio
    ".mp3": "Audio", ".flac": "Audio", ".wav": "Audio", ".aac": "Audio",
    ".ogg": "Audio", ".m4a": "Audio",
    # Archive
    ".zip": "Archive", ".tar": "Archive", ".gz": "Archive", ".bz2": "Archive",
    ".xz": "Archive", ".7z": "Archive", ".rar": "Archive", ".deb": "Archive",
    ".rpm": "Archive", ".iso": "Disk Image",
    # Documents
    ".pdf": "Document", ".doc": "Document", ".docx": "Document",
    ".odt": "Document", ".xls": "Document", ".xlsx": "Document",
    ".ppt": "Document", ".pptx": "Document",
    # Code
    ".py": "Code", ".js": "Code", ".ts": "Code", ".c": "Code", ".cpp": "Code",
    ".h": "Code", ".rs": "Code", ".go": "Code", ".java": "Code",
    # Log
    ".log": "Log", ".journal": "Log",
    # AppImage
    ".appimage": "AppImage",
}


def _file_type(path: Path) -> str:
    return _EXT_TYPES.get(path.suffix.lower(), "Other")


def _file_hash(path: Path, chunk: int = 65536) -> str | None:
    """SHA-256 of first 256 KB — fast enough for practical duplicate detection."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            data = f.read(chunk * 4)
            h.update(data)
        return h.hexdigest()
    except OSError:
        return None


class Analyzer:
    """
    Read-only analyzer. All methods return data — nothing is deleted.

    progress_callback(message, percent) is called during long operations.
    scan_root limits the scan to a specific directory (defaults to HOME).
    """

    def __init__(
        self,
        progress_callback: Callable[[str, int], None] | None = None,
        scan_root: Path = HOME,
    ):
        self._progress = progress_callback or (lambda m, p: None)
        self._scan_root = scan_root

    # ── Public API ─────────────────────────────────────────────

    def largest_files(self, top_n: int = 50, min_size_mb: float = 1.0) -> list[FileEntry]:
        """Return top_n largest files under scan_root, >= min_size_mb."""
        min_bytes = int(min_size_mb * 1024 * 1024)
        entries: list[FileEntry] = []

        self._progress("Scanning for large files…", 0)
        total_seen = 0

        for root, dirs, files in os.walk(self._scan_root):
            root_path = Path(root)
            # Prune skipped roots
            dirs[:] = [
                d for d in dirs
                if str(root_path / d) not in SKIP_ROOTS
                and not (root_path / d).is_symlink()
            ]
            for fname in files:
                fpath = root_path / fname
                try:
                    if fpath.is_symlink():
                        continue
                    size = fpath.stat().st_size
                    if size >= min_bytes:
                        mtime = datetime.fromtimestamp(fpath.stat().st_mtime)
                        entries.append(FileEntry(
                            path=fpath,
                            size=size,
                            modified=mtime,
                            file_type=_file_type(fpath),
                        ))
                    total_seen += 1
                    if total_seen % 5000 == 0:
                        self._progress(f"Scanned {total_seen:,} files…", min(90, total_seen // 500))
                except (PermissionError, OSError):
                    continue

        self._progress("Sorting results…", 95)
        entries.sort(key=lambda e: e.size, reverse=True)
        self._progress("Done.", 100)
        return entries[:top_n]

    def largest_folders(self, top_n: int = 20) -> list[FolderEntry]:
        """Return top_n largest immediate subdirectories of scan_root."""
        self._progress("Scanning folder sizes…", 0)
        entries: list[FolderEntry] = []

        try:
            subdirs = [p for p in self._scan_root.iterdir() if p.is_dir() and not p.is_symlink()]
        except PermissionError:
            return []

        for i, subdir in enumerate(subdirs):
            self._progress(f"Measuring {subdir.name}…", int(i / max(len(subdirs), 1) * 90))
            size, count = _dir_size(subdir)
            entries.append(FolderEntry(path=subdir, size=size, file_count=count))

        entries.sort(key=lambda e: e.size, reverse=True)
        self._progress("Done.", 100)
        return entries[:top_n]

    def find_duplicates(self, method: str = "hash") -> list[DuplicateGroup]:
        """
        Find duplicate files.
        method: "hash" (accurate, slower) | "name_size" (fast, approximate)
        """
        self._progress("Indexing files for duplicate scan…", 0)

        if method == "hash":
            return self._find_dupes_by_hash()
        return self._find_dupes_by_name_size()

    def broken_symlinks(self) -> list[Path]:
        """Return list of broken symlinks under scan_root."""
        self._progress("Scanning for broken symlinks…", 0)
        broken = []
        for root, dirs, files in os.walk(self._scan_root):
            root_path = Path(root)
            dirs[:] = [d for d in dirs if not (root_path / d).is_symlink()]
            for name in files + dirs:
                p = root_path / name
                try:
                    if p.is_symlink() and not p.exists():
                        broken.append(p)
                except OSError:
                    continue
        self._progress("Done.", 100)
        return broken

    def file_type_breakdown(self, entries: list[FileEntry]) -> dict[str, int]:
        """Aggregate total bytes per file type from a list of FileEntry."""
        breakdown: dict[str, int] = {}
        for e in entries:
            breakdown[e.file_type] = breakdown.get(e.file_type, 0) + e.size
        return dict(sorted(breakdown.items(), key=lambda x: x[1], reverse=True))

    # ── Internals ──────────────────────────────────────────────

    def _find_dupes_by_hash(self) -> list[DuplicateGroup]:
        # Step 1: group by size (quick pre-filter)
        size_map: dict[int, list[Path]] = {}
        seen = 0
        for root, dirs, files in os.walk(self._scan_root):
            root_path = Path(root)
            dirs[:] = [d for d in dirs if not (root_path / d).is_symlink()]
            for fname in files:
                p = root_path / fname
                try:
                    if p.is_symlink():
                        continue
                    sz = p.stat().st_size
                    if sz < 1024:           # skip tiny files
                        continue
                    size_map.setdefault(sz, []).append(p)
                    seen += 1
                    if seen % 5000 == 0:
                        self._progress(f"Indexed {seen:,} files…", min(40, seen // 1000))
                except OSError:
                    continue

        # Step 2: hash only files that share a size
        candidates = {sz: paths for sz, paths in size_map.items() if len(paths) > 1}
        hash_map: dict[str, list[Path]] = {}
        done = 0
        total = sum(len(v) for v in candidates.values())

        for sz, paths in candidates.items():
            for p in paths:
                h = _file_hash(p)
                if h:
                    hash_map.setdefault(h, []).append(p)
                done += 1
                if done % 100 == 0:
                    pct = 40 + int(done / max(total, 1) * 55)
                    self._progress(f"Hashing {done}/{total}…", pct)

        groups = [
            DuplicateGroup(key=h, files=ps, size=ps[0].stat().st_size)
            for h, ps in hash_map.items()
            if len(ps) > 1
        ]
        groups.sort(key=lambda g: g.wasted, reverse=True)
        self._progress("Done.", 100)
        return groups

    def _find_dupes_by_name_size(self) -> list[DuplicateGroup]:
        key_map: dict[str, list[Path]] = {}
        for root, dirs, files in os.walk(self._scan_root):
            root_path = Path(root)
            dirs[:] = [d for d in dirs if not (root_path / d).is_symlink()]
            for fname in files:
                p = root_path / fname
                try:
                    sz = p.stat().st_size
                    k = f"{fname}::{sz}"
                    key_map.setdefault(k, []).append(p)
                except OSError:
                    continue

        groups = [
            DuplicateGroup(key=k, files=ps, size=ps[0].stat().st_size)
            for k, ps in key_map.items()
            if len(ps) > 1
        ]
        groups.sort(key=lambda g: g.wasted, reverse=True)
        self._progress("Done.", 100)
        return groups


def _dir_size(path: Path) -> tuple[int, int]:
    total, count = 0, 0
    try:
        for entry in os.scandir(path):
            try:
                if entry.is_symlink():
                    continue
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
                    count += 1
                elif entry.is_dir(follow_symlinks=False):
                    s, c = _dir_size(Path(entry.path))
                    total += s
                    count += c
            except OSError:
                continue
    except (PermissionError, OSError):
        pass
    return total, count
