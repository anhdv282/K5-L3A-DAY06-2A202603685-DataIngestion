"""Download the OpenStax PDF listed in data/manifest.csv and verify its sha256.

Usage (from starter/):  python scripts/fetch_corpus.py
"""
import csv
import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data" / "manifest.csv"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    with MANIFEST.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    seen = set()
    for row in rows:
        target = ROOT / row["local_path"]
        if target in seen:
            continue
        seen.add(target)
        if target.exists() and sha256(target) == row["sha256"]:
            print(f"OK (cached) {target}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {row['source_path_or_url']} -> {target}")
        request = urllib.request.Request(row["source_path_or_url"], headers={"User-Agent": "course-lab-fetch/1.0"})
        with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as out:
            while block := response.read(1 << 20):
                out.write(block)
        digest = sha256(target)
        if digest != row["sha256"]:
            sys.exit(f"sha256 mismatch for {target}: {digest} (OpenStax may have published a new version)")
        print(f"OK {target}")


if __name__ == "__main__":
    main()
