#!/usr/bin/env python
"""Fetch the official Solidity compiler into this repo's gitignored cache, and verify it.

    python scripts/install_solc.py

📛 **A DOWNLOADED EXECUTABLE NEEDS A CRITERION, NOT A SOURCE YOU TRUST.** This pulls a binary and
then runs it, which is the one thing in this repository that could do real harm if it were the wrong
bytes. So the version is PINNED, the SHA-256 is PINNED here in the source, and the file is deleted
rather than used if it does not match. "It came from the official domain over HTTPS" is not the
check -- the check is that the bytes hash to a value written down before the download happened.

⭐ It installs into `cache/solc/`, not onto the system. Nothing outside this directory changes, the
`cache/` path is gitignored, and removing the compiler is `rm -rf cache/solc`.
"""

from __future__ import annotations

import hashlib
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Final

#: Pinned 2026-09-20 from https://binaries.soliditylang.org/linux-amd64/list.json (latestRelease).
SOLC_VERSION: Final[str] = "0.8.37"
SOLC_FILE: Final[str] = "solc-linux-amd64-v0.8.37+commit.f401782d"
SOLC_SHA256: Final[str] = "5de843c2c93563cc66425c99a4fb13fdbf32b4c4ae07469480faaf126e14404a"
SOLC_URL: Final[str] = f"https://binaries.soliditylang.org/linux-amd64/{SOLC_FILE}"

INSTALL_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "cache" / "solc"
SOLC_PATH: Final[Path] = INSTALL_DIR / f"solc-{SOLC_VERSION}"


def solc_binary() -> Path:
    """The compiler, downloading and verifying it if this is the first call. Importable."""
    if SOLC_PATH.exists() and hashlib.sha256(SOLC_PATH.read_bytes()).hexdigest() == SOLC_SHA256:
        return SOLC_PATH

    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    print(f"downloading solc {SOLC_VERSION}...")
    # ⚠️ The CDN answers Python's default User-Agent with 403, so one is set. That is a header, not
    # a trust decision: the bytes are still only accepted if they hash to the pin below.
    request = urllib.request.Request(SOLC_URL, headers={"User-Agent": "curl/8.5.0"})
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = response.read()

    digest = hashlib.sha256(payload).hexdigest()
    if digest != SOLC_SHA256:
        # ⚠️ Nothing is written to disk on a mismatch. A quarantined copy "for inspection" is still
        # a copy of an unidentified executable sitting in the tree.
        raise SystemExit(f"SHA-256 mismatch\n  expected {SOLC_SHA256}\n  got      {digest}\nrefusing to install")

    SOLC_PATH.write_bytes(payload)
    SOLC_PATH.chmod(SOLC_PATH.stat().st_mode | stat.S_IXUSR)
    print(f"verified sha256 {digest}")
    return SOLC_PATH


def main() -> int:
    path = solc_binary()
    reported = subprocess.run([str(path), "--version"], capture_output=True, text=True, check=True).stdout.strip()
    print(f"\n{path}")
    print(reported)
    # ⭐ The binary's own version string has to agree with the pin. A hash match proves the bytes
    # are the ones expected; this proves the ones expected are the ones wanted.
    if SOLC_VERSION not in reported:
        raise SystemExit(f"binary reports a version that is not the pinned {SOLC_VERSION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
