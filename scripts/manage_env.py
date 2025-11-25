"""Utility CLI for managing .env files without relying on external tooling."""

from __future__ import annotations

import argparse
import secrets
from pathlib import Path
from typing import Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
EXAMPLE_ENV_FILE = PROJECT_ROOT / ".env.example"
SENSITIVE_KEYS = {"SECRET_KEY", "OPENAI_API_KEY"}


def _parse_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _write_env_file(path: Path, values: Dict[str, str]) -> None:
    ordered = sorted(values.items())
    payload = "\n".join(f"{key}={value}" for key, value in ordered)
    path.write_text(payload + "\n")


def resolve_env_path(env: str | None, explicit_file: str | None) -> Path:
    if explicit_file:
        return Path(explicit_file).expanduser().resolve()
    if not env or env.lower() in {"", "development"}:
        return DEFAULT_ENV_FILE
    return PROJECT_ROOT / f".env.{env.lower()}"


def _ensure_secret(existing: Dict[str, str], force: bool) -> None:
    if force or not existing.get("SECRET_KEY") or existing["SECRET_KEY"].startswith("changeme"):
        existing["SECRET_KEY"] = secrets.token_urlsafe(32)


def read_env_values(path: Path) -> Dict[str, str]:
    return _parse_env_file(path)


def prepare_env_file(
    env: str | None = None,
    file: str | None = None,
    force: bool = False,
    new_secret: bool = False,
) -> Path:
    target = resolve_env_path(env, file)
    example_values = _parse_env_file(EXAMPLE_ENV_FILE)
    target_values = _parse_env_file(target)
    merged = dict(example_values) if force else {**example_values, **target_values}
    if env:
        merged["ENV"] = env
    _ensure_secret(merged, force=new_secret)
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_env_file(target, merged)
    return target


def cmd_init(args: argparse.Namespace) -> None:
    target = resolve_env_path(args.env, args.file)
    if target.exists():
        action = "Overwriting" if args.force else "Updating"
    else:
        action = "Creating"
    print(f"{action} env file at {target}")
    prepare_env_file(args.env, args.file, force=args.force, new_secret=args.new_secret)


def cmd_set(args: argparse.Namespace) -> None:
    target = resolve_env_path(args.env, args.file)
    values = _parse_env_file(target)
    values[args.key] = args.value
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_env_file(target, values)
    print(f"Set {args.key} in {target}")


def cmd_show(args: argparse.Namespace) -> None:
    target = resolve_env_path(args.env, args.file)
    values = _parse_env_file(target)
    if not values:
        print(f"No values found in {target}")
        return
    for key, value in sorted(values.items()):
        if args.redact and key in SENSITIVE_KEYS:
            print(f"{key}=***redacted***")
        else:
            print(f"{key}={value}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage project .env files")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--env", help="Named environment, maps to .env.<env>")
    common.add_argument("--file", help="Explicit path to an env file")

    init_cmd = sub.add_parser("init", parents=[common], help="Create or sync an env file")
    init_cmd.add_argument("--force", action="store_true", help="Overwrite the target file")
    init_cmd.add_argument(
        "--new-secret", action="store_true", help="Generate a fresh SECRET_KEY even if one exists"
    )
    init_cmd.set_defaults(func=cmd_init)

    set_cmd = sub.add_parser("set", parents=[common], help="Set a single key/value pair")
    set_cmd.add_argument("key")
    set_cmd.add_argument("value")
    set_cmd.set_defaults(func=cmd_set)

    show_cmd = sub.add_parser("show", parents=[common], help="Show the contents of an env file")
    show_cmd.add_argument("--redact", action="store_true", help="Hide sensitive values")
    show_cmd.set_defaults(func=cmd_show)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
