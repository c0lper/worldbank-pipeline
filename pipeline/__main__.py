"""Command line entry point.

    python -m pipeline run        fetch from the API and rebuild silver + gold + reports
    python -m pipeline rebuild    reprocess the latest raw responses already stored (no API calls)
"""
from __future__ import annotations

import argparse
import logging
import sys

from .config import load_config
from .run import run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline", description="World Bank indicators pipeline")
    parser.add_argument("command", choices=["run", "rebuild"])
    parser.add_argument("--config", default="config.yaml", help="path to config file (default: config.yaml)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    cfg = load_config(args.config)
    try:
        run(cfg, rebuild_only=(args.command == "rebuild"))
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
