from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cua import __version__
from cua.config import load_settings
from cua.contracts.artifact import Capability
from cua.store.files import FileCapabilityStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cua",
        description="Discover-once / replay-many computer-use automation",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("version", help="print package version")

    p_val = sub.add_parser("validate", help="validate a capability JSON file")
    p_val.add_argument("path")

    sub.add_parser("list", help="list saved capabilities")

    p_get = sub.add_parser("show", help="print a capability")
    p_get.add_argument("id")

    args = parser.parse_args(argv)
    settings = load_settings()
    store = FileCapabilityStore(settings.capabilities_dir)

    if args.cmd == "version":
        print(__version__)
        return 0
    if args.cmd == "validate":
        cap = Capability.model_validate_json(Path(args.path).read_text(encoding="utf-8"))
        print(f"ok {cap.id} v{cap.version} steps={len(cap.steps)} risk={cap.risk_class.value}")
        return 0
    if args.cmd == "list":
        for cap in store.list():
            print(f"{cap.id}\tv{cap.version}\t{cap.name}\t{cap.vendor_app}")
        return 0
    if args.cmd == "show":
        cap = store.get(args.id)
        print(json.dumps(json.loads(cap.model_dump_json()), indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
