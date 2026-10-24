"""Run with the installed research environment and a Linux Docker socket.

Example: python scripts/freeze_environments.py --root /source
No lock is published until all pulls, builds and offline version probes succeed.
"""

import argparse
from contextlib import closing
from pathlib import Path

import docker

from robo_minerador.environments.freeze import freeze


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    with closing(docker.from_env()) as client:
        lock = freeze(client, args.root.resolve())
    print(f"Frozen {len(lock.bases)} bases and {len(lock.runtimes)} runtimes ({lock.platform})")


if __name__ == "__main__":
    main()
