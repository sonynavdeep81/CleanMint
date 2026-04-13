"""
core/printer.py — Printer Profile Engine

Reads CUPS printer configuration and generates a portable restore script.
Works entirely via user-level CUPS commands (no sudo needed for reading).
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PrinterInfo:
    name: str           # CUPS queue name
    model: str          # Make and model string
    device_uri: str     # e.g. ipp://Brother%20DCP-L2540DW...
    connection: str     # "Network (IPP)" | "USB" | "Unknown"
    driver_pkg: str     # apt package name e.g. printer-driver-brlaser
    enabled: bool
    toner_pct: int | None   # 0-100 or None if unknown


@dataclass
class ServiceStatus:
    cups: bool
    cups_browsed: bool
    avahi: bool


def _run(cmd: list[str], timeout: int = 15) -> str:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        ).stdout
    except Exception:
        return ""


def _is_service_active(name: str) -> bool:
    try:
        r = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True, text=True,
        )
        return r.stdout.strip() == "active"
    except Exception:
        return False


def _connection_type(uri: str) -> str:
    if uri.startswith("ipp://") or uri.startswith("ipps://"):
        return "Network (IPP/Wi-Fi)"
    if uri.startswith("implicitclass://"):
        return "Network (Wi-Fi)"
    if uri.startswith("usb://"):
        return "USB"
    if uri.startswith("socket://") or uri.startswith("lpd://"):
        return "Network (TCP)"
    return "Unknown"


def _detect_driver_package() -> str:
    """Return the installed Brother driver apt package name."""
    out = _run(["dpkg", "-l"])
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        pkg = parts[1]
        if "brlaser" in pkg or "brother" in pkg.lower():
            return pkg
    return "printer-driver-brlaser"   # safe default


def _get_toner(printer_name: str) -> int | None:
    """Try to read toner level via CUPS marker attributes."""
    out = _run(["ipptool", "-tv", f"ipp://localhost/printers/{printer_name}",
                "/usr/share/cups/ipptool/get-printer-attributes.test"])
    for line in out.splitlines():
        if "marker-levels" in line:
            parts = line.split("=")
            if len(parts) == 2:
                try:
                    return int(parts[1].strip())
                except ValueError:
                    pass
    return None


def get_printers() -> list[PrinterInfo]:
    """
    Return a list of properly configured (enabled) CUPS printers,
    skipping disabled/placeholder entries.
    """
    driver_pkg = _detect_driver_package()
    printers: list[PrinterInfo] = []
    seen_uris: set[str] = set()

    # lpstat -p gives one line per queue with enabled/disabled status
    lp_out = _run(["lpstat", "-p", "-l"])
    queues: list[tuple[str, bool]] = []
    for line in lp_out.splitlines():
        if line.startswith("printer "):
            parts = line.split()
            name    = parts[1]
            enabled = "disabled" not in line
            queues.append((name, enabled))

    for name, enabled in queues:
        # Skip disabled printers — they are placeholders, not working printers
        if not enabled:
            continue

        # Get device URI + model from lpoptions
        # lpoptions output: key=value pairs, values may be quoted with spaces
        opts_out = _run(["lpoptions", "-p", name])
        uri   = ""
        model = ""
        # Use a proper key=value parser that handles quoted strings
        import re
        for m in re.finditer(r'(\w[\w-]*)=("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|\S+)',
                             opts_out):
            key, val = m.group(1), m.group(2).strip("'\"")
            if key == "device-uri":
                uri = val
            elif key == "printer-make-and-model":
                model = val

        # Deduplicate: skip if we already have this physical device
        uri_key = uri.lower()
        if uri_key in seen_uris:
            continue
        seen_uris.add(uri_key)

        printers.append(PrinterInfo(
            name=name,
            model=model or name,
            device_uri=uri,
            connection=_connection_type(uri),
            driver_pkg=driver_pkg,
            enabled=enabled,
            toner_pct=_get_toner(name),
        ))

    return printers


def get_service_status() -> ServiceStatus:
    return ServiceStatus(
        cups=_is_service_active("cups"),
        cups_browsed=_is_service_active("cups-browsed"),
        avahi=_is_service_active("avahi-daemon"),
    )


def generate_restore_script(printers: list[PrinterInfo], dest: Path) -> None:
    """
    Write a printer restore script to `dest`.
    Reinstalls driver, starts required services, re-adds each printer via lpadmin.
    """
    driver_pkgs = sorted({p.driver_pkg for p in printers})

    lines = [
        "#!/usr/bin/env bash",
        "# Printer Restore Script — generated by CleanMint",
        "# Run on any Ubuntu/Debian machine to restore your printer setup:",
        "#   bash printer_restore.sh",
        "",
        "set -e",
        "",
        "# ── 1. Install printer driver ────────────────────────────────────────",
        "sudo apt-get update -qq",
    ]
    for pkg in driver_pkgs:
        lines.append(f"sudo apt-get install -y {pkg} || true")

    lines += [
        "",
        "# ── 2. Enable required services ─────────────────────────────────────",
        "sudo systemctl enable --now cups avahi-daemon || true",
        "sudo systemctl enable --now cups-browsed || true",
        "",
        "# ── 3. Add printer queue(s) ──────────────────────────────────────────",
    ]

    for p in printers:
        lines += [
            f'echo "Adding printer: {p.name}"',
            f'sudo lpadmin -p "{p.name}" \\',
            f'    -E \\',
            f'    -v "{p.device_uri}" \\',
            f'    -m everywhere \\',
            f'    -D "{p.model}" || true',
            f'sudo cupsenable "{p.name}" || true',
            f'sudo cupsaccept "{p.name}" || true',
            "",
        ]

    # Set the first printer as default
    if printers:
        lines.append(f'sudo lpadmin -d "{printers[0].name}"')
        lines.append("")

    lines += [
        "# ── 4. Restart CUPS to apply ─────────────────────────────────────────",
        "sudo systemctl restart cups",
        "",
        'echo ""',
        'echo "Printer restore complete!"',
        f'echo "  Printers added: {len(printers)}"',
        'echo "  Open Settings → Printers to verify."',
    ]

    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    dest.chmod(0o755)
