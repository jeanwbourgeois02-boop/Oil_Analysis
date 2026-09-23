"""
Umgebungspruefung und Selbstreparatur - laeuft NUR mit der Standardbibliothek.

Dieses Modul wird vor allen anderen Importen ausgefuehrt. Es prueft die
Python-Version, die Rechenpakete und (optional) die Bloomberg-Desktop-API und
installiert fehlende Pakete nach.

    py tools/bootstrap.py              # pruefen und fehlende Pakete installieren
    py tools/bootstrap.py --check      # nur pruefen, nichts installieren
    py tools/bootstrap.py --no-bloomberg

Rueckgabe/Exit: 0 = Rechenpakete vollstaendig (Bloomberg optional),
                1 = Rechenpakete fehlen und konnten nicht installiert werden.
"""
from __future__ import annotations

import argparse
import importlib
import subprocess
import sys
from pathlib import Path

MIN_PYTHON = (3, 10)

# Rechenkern: ohne diese Pakete laeuft die Analyse nicht.
CORE = [
    ("pandas", "pandas>=2.0"),
    ("numpy", "numpy"),
    ("openpyxl", "openpyxl"),
    ("matplotlib", "matplotlib"),
    ("plotly", "plotly"),
]

# Bloomberg-Desktop-API: nur auf dem Bloomberg-PC noetig, Analyse laeuft auch ohne.
BLPAPI_INDEX = "https://blpapi.bloomberg.com/repository/releases/python/simple/"
BLOOMBERG = [
    ("blpapi", ["blpapi", "--index-url", BLPAPI_INDEX]),
    ("xbbg", ["xbbg"]),
]


def have(module: str) -> bool:
    """True, wenn der Import klappt. blpapi kann auch mit DLL-Fehlern scheitern."""
    try:
        importlib.import_module(module)
        return True
    except Exception:  # noqa: BLE001
        return False


def _user_flag() -> list[str]:
    """--user nur ausserhalb einer venv; in einer venv ist es ein Fehler."""
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    return [] if in_venv else ["--user"]


def pip_install(spec: list[str], label: str) -> bool:
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
           *_user_flag(), *spec]
    print(f"    installiere {label} ...", flush=True)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except Exception as exc:  # noqa: BLE001
        print(f"    FEHLER: pip nicht ausfuehrbar ({exc})")
        return False
    if res.returncode == 0:
        importlib.invalidate_caches()
        return True
    tail = (res.stderr or res.stdout or "").strip().splitlines()
    for line in tail[-4:]:
        print(f"      {line}")
    return False


def check_python() -> bool:
    ok = sys.version_info[:2] >= MIN_PYTHON
    need = ".".join(str(x) for x in MIN_PYTHON)
    have_v = ".".join(str(x) for x in sys.version_info[:3])
    print(f"  Python {have_v:<10} {'ok' if ok else f'zu alt, benoetigt >= {need}'}")
    if not ok:
        print(f"  -> Bitte Python {need} oder neuer installieren (https://www.python.org/downloads/).")
    return ok


def ensure(pkgs: list[tuple[str, object]], install: bool) -> list[str]:
    """Prueft und installiert; gibt die Namen zurueck, die danach immer noch fehlen."""
    missing = []
    for module, spec in pkgs:
        if have(module):
            try:
                version = getattr(importlib.import_module(module), "__version__", "?")
            except Exception:  # noqa: BLE001
                version = "?"
            print(f"  {module:<12} {version:<10} ok")
            continue
        if not install:
            print(f"  {module:<12} {'-':<10} fehlt")
            missing.append(module)
            continue
        print(f"  {module:<12} {'-':<10} fehlt")
        spec_list = spec if isinstance(spec, list) else [spec]
        if pip_install(spec_list, module) and have(module):
            print(f"  {module:<12} {'':<10} nachinstalliert")
        else:
            missing.append(module)
    return missing


def run(install: bool = True, bloomberg: bool = True) -> tuple[bool, bool]:
    """(core_ok, bloomberg_ok)."""
    print("Umgebung")
    py_ok = check_python()
    print("\nRechenpakete")
    core_missing = ensure(CORE, install)
    core_ok = py_ok and not core_missing

    bbg_ok = False
    if bloomberg:
        print("\nBloomberg-Desktop-API (nur auf dem Bloomberg-PC noetig)")
        if have("blpapi") or have("xbbg"):
            ensure([p for p in BLOOMBERG], False)
            bbg_ok = True
        elif install:
            for module, spec in BLOOMBERG:
                print(f"  {module:<12} {'-':<10} fehlt")
                if pip_install(spec, module) and have(module):
                    print(f"  {module:<12} {'':<10} nachinstalliert")
                    bbg_ok = True
                    break
            if not bbg_ok:
                print("  -> Keine Bloomberg-API verfuegbar. Das ist nur auf dem Bloomberg-PC")
                print("     ein Problem; die Analyse laeuft mit bereits geholten Daten weiter.")
        else:
            ensure([p for p in BLOOMBERG], False)

    print()
    if not core_ok:
        print(f"Rechenpakete unvollstaendig: {', '.join(core_missing)}")
    return core_ok, bbg_ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Umgebung pruefen und fehlende Pakete nachinstallieren")
    ap.add_argument("--check", action="store_true", help="nur pruefen, nichts installieren")
    ap.add_argument("--no-bloomberg", action="store_true", help="Bloomberg-API nicht pruefen")
    args = ap.parse_args(argv)
    core_ok, _ = run(install=not args.check, bloomberg=not args.no_bloomberg)
    return 0 if core_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
