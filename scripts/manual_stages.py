"""Manual discovery/screening/extraction checkpoints, using production services.

Static extraction requires this project's healthy Compose analyzer. Prepare it with:
docker compose --profile analyzer up -d --wait analyzer
Build explicitly after analyzer changes: docker compose --profile analyzer build analyzer
No repository scripts are executed. Checkpoints are local evidence, not accepted datasets.
"""

import argparse
import json
import sys
from pathlib import Path

from _manual.checkpoints import CheckpointIntegrityError, read_checkpoint
from _manual.checkpoints import latest_checkpoint as latest_checkpoint
from _manual.credentials import load_token as load_token
from _manual.errors import SAFE_HINTS, ManualInputError
from _manual.stages import run_stage as run_stage


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("discovery", "screening", "extraction", "all"))
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument("--input-json", type=Path)
    parser.add_argument("--created-from", default="2024-01-01T00:00:00Z")
    parser.add_argument("--created-until", default="2024-01-01T00:05:00Z")
    parser.add_argument(
        "--max-repos", type=int, default=None, help="New selection limit (default 10)"
    )
    parser.add_argument(
        "--build-analyzer", action="store_true", help="Explicit Compose analyzer build"
    )
    args = parser.parse_args(argv)
    try:
        output = args.output_directory or args.project_root / "results/manual-stages"
        input_json = args.input_json
        if args.action in {"screening", "extraction"} and input_json is None:
            previous = "discovery" if args.action == "screening" else "screening"
            input_json = latest_checkpoint(args.project_root, output, previous)
        elif args.action == "screening":
            # Refuse explicit invalid checkpoints before requesting network credentials.
            read_checkpoint(input_json, args.project_root, output)
        token = "" if args.action == "extraction" else load_token(args.project_root)
        path = run_stage(
            args.action,
            args.project_root,
            output,
            token=token,
            input_json=input_json,
            created_from=args.created_from,
            created_until=args.created_until,
            max_repos=args.max_repos,
            build_analyzer=args.build_analyzer,
        )
        value = json.loads(path.read_bytes())
        print(f"status={value['status']} run_id={value['run_id']}")
        for name in ("discovery", "screening", "extraction", "result"):
            candidate = path.parent / (name + ".json")
            if candidate.is_file():
                print(f"{name}: {candidate}")
        if value["status"] == "interrupted":
            return 4 if value["error"]["code"] == "integrity_failure" else 3
        return 0 if value["status"] == "completed" else 3
    except CheckpointIntegrityError:
        print("Não iniciado: integrity_failure no checkpoint manual.", file=sys.stderr)
        return 4
    except ManualInputError as error:
        hint = SAFE_HINTS.get(error.code, "Argumentos manuais inválidos; consulte -Help.")
        print(hint, file=sys.stderr)
        return 2
    except ValueError as error:
        # Never echo exception text, HTTP headers, configuration or token values.
        print(
            f"Não iniciado: argumentos/token/checkpoint inválido ({type(error).__name__}).",
            file=sys.stderr,
        )
        return 2
    except (Exception, KeyboardInterrupt) as error:
        print(f"infrastructure_interrupted ({type(error).__name__}).", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
