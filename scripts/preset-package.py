"""Create and sign DataForge preset packages.

    python scripts/preset-package.py generate-key --out %USERPROFILE%\\.dataforge\\keys\\presets.pem
    python scripts/preset-package.py build --name vendor-pack --version 1.0.0 --preset path\\to\\preset.json [--preset ...] \\
        --key %USERPROFILE%\\.dataforge\\keys\\presets.pem --out vendor-pack-1.0.0.dfpreset

Keep private keys out of the repository. To trust a key on a machine, add its entry to
%LOCALAPPDATA%\\DataForge\\trusted-preset-keys.json; to trust it for every install, add it to
packages/presets/trusted_keys.json after review.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workers" / "scraping" / "src"))

from dataforge_scraping.packages import generate_key, run_health_check, sign_package  # noqa: E402
from dataforge_scraping.presets import validate_preset  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    key = commands.add_parser("generate-key")
    key.add_argument("--out", type=Path, required=True)
    build = commands.add_parser("build")
    build.add_argument("--name", required=True)
    build.add_argument("--version", required=True)
    build.add_argument("--preset", type=Path, action="append", required=True)
    build.add_argument("--key", type=Path, required=True)
    build.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "generate-key":
        if args.out.exists():
            raise SystemExit(f"{args.out} already exists; refusing to overwrite a signing key")
        if ROOT in args.out.resolve().parents:
            raise SystemExit("Refusing to write a private key inside the repository")
        info = generate_key(args.out)
        print(json.dumps({info["key_id"]: info["public_key_pem"]}, indent=2))
        print(f"Private key written to {args.out}. Add the JSON above to a trusted-preset-keys.json file.", file=sys.stderr)
        return

    presets, fixtures = [], {}
    for path in args.preset:
        preset = json.loads(path.read_text(encoding="utf-8-sig"))
        errors = validate_preset(preset)
        if errors:
            raise SystemExit(f"{path}: {'; '.join(errors)}")
        for fixture in (preset.get("health") or {}).get("fixture_tests", []):
            for base in (path.parent, ROOT / "packages" / "presets"):
                if (base / fixture).is_file():
                    fixtures[fixture] = (base / fixture).read_text(encoding="utf-8-sig")
                    break
        result = run_health_check(preset, fixtures)
        if result["status"] != "passed":
            raise SystemExit(f"{preset['id']} fails its fixture health check: {'; '.join(result['failures'])}")
        presets.append(preset)
    signed = sign_package({"name": args.name, "version": args.version, "presets": presets, "fixtures": fixtures}, args.key.read_bytes())
    args.out.write_text(json.dumps(signed, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {args.out} ({len(presets)} presets, key {signed['key_id']})")


if __name__ == "__main__":
    main()
