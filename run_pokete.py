#!/usr/bin/env python3
"""pokete-tui — Textual re-shell over lxgr-linux/pokete.

Run with `.venv/bin/python pokete.py` or `make run`. Flags:
  --save-dir DIR   override Pokete's save directory
  --agent PORT     start the REST agent API on 127.0.0.1:PORT
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="pokete-tui",
        description="Textual re-shell over lxgr-linux/pokete",
    )
    parser.add_argument(
        "--save-dir", type=Path, default=None,
        help="override Pokete's save directory (default: ~/.config/pokete)",
    )
    parser.add_argument(
        "--agent", type=int, default=None, metavar="PORT",
        help="start the REST agent API on 127.0.0.1:PORT (default off)",
    )
    args = parser.parse_args()

    from pokete_tui.app import run
    run(save_dir=args.save_dir, agent_port=args.agent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
