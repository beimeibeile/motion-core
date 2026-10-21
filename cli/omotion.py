#!/usr/bin/env python3
"""O.motion CLI - Command line interface for .motion template operations."""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="omotion",
        description="O.motion - .motion template authoring & batch production toolkit"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # render command
    render_parser = subparsers.add_parser("render", help="Render a .motion template")
    render_parser.add_argument("template", help="Path to .motion template")
    render_parser.add_argument("--params", help="Path to params JSON file")
    render_parser.add_argument("--output", "-o", help="Output MP4 path")
    render_parser.add_argument("--preview", action="store_true", help="Low-res preview")

    # inspect command
    inspect_parser = subparsers.add_parser("inspect", help="Inspect a .motion template")
    inspect_parser.add_argument("template", help="Path to .motion template")

    # validate command
    validate_parser = subparsers.add_parser("validate", help="Validate template and params")
    validate_parser.add_argument("template", help="Path to .motion template")
    validate_parser.add_argument("--params", help="Path to params JSON file")

    # batch command
    batch_parser = subparsers.add_parser("batch", help="Batch render from task list")
    batch_parser.add_argument("tasklist", help="Path to task list JSON")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    # Commands will be implemented in Phase 0+
    print(f"Command '{args.command}' will be implemented in Phase 0+")
    print(f"Args: {args}")


if __name__ == "__main__":
    main()
