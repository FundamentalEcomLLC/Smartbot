"""Deployment orchestration helpers (Python-only)."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Tuple

from manage_env import prepare_env_file, read_env_values, resolve_env_path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_runtime_env(env_name: str | None, env_file: str | None, force_prepare: bool) -> Tuple[Dict[str, str], Path]:
    target = resolve_env_path(env_name, env_file)
    if force_prepare or not target.exists():
        prepare_env_file(env_name, env_file, force=force_prepare)
    values = read_env_values(target)
    runtime = os.environ.copy()
    runtime.update(values)
    if env_name:
        runtime["ENV"] = env_name
    runtime.setdefault("ENV", values.get("ENV", "development"))
    runtime.setdefault("CHATBOT_ENV_FILE", str(target))
    return runtime, target


def _run(cmd: list[str], env: Dict[str, str]) -> None:
    pretty = " ".join(cmd)
    print(f"→ {pretty}")
    subprocess.run(cmd, check=True, cwd=PROJECT_ROOT, env=env)


def cmd_prepare(args: argparse.Namespace) -> None:
    target = prepare_env_file(args.env, args.file, force=args.force, new_secret=args.new_secret)
    print(f"Env file ready: {target}")


def cmd_migrate(args: argparse.Namespace) -> None:
    env, env_file = _load_runtime_env(args.env, args.file, args.ensure_env)
    print(f"Running migrations using {env_file}")
    _run([sys.executable, "-m", "alembic", "upgrade", args.revision], env)


def cmd_start(args: argparse.Namespace) -> None:
    env, env_file = _load_runtime_env(args.env, args.file, args.ensure_env)
    print(f"Starting API using {env_file}")
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    if args.workers:
        cmd.extend(["--workers", str(args.workers)])
    if args.reload:
        cmd.append("--reload")
    _run(cmd, env)


def cmd_deploy(args: argparse.Namespace) -> None:
    env, env_file = _load_runtime_env(args.env, args.file, args.ensure_env)
    print(f"Environment ready: {env_file}")
    if not args.skip_migrate:
        _run([sys.executable, "-m", "alembic", "upgrade", args.revision], env)
    auto_skip_start = bool(os.environ.get("CI")) and not args.force_start
    if auto_skip_start:
        print("CI detected, skipping uvicorn startup (use --force-start to disable this).")
    if not args.skip_start and not auto_skip_start:
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            args.host,
            "--port",
            str(args.port),
        ]
        if args.workers:
            cmd.extend(["--workers", str(args.workers)])
        if args.reload:
            cmd.append("--reload")
        _run(cmd, env)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deployment utilities")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--env", help="Named environment (maps to .env.<env>)")
    common.add_argument("--file", help="Explicit env file path")

    prep = sub.add_parser("prepare", parents=[common], help="Ensure env file exists")
    prep.add_argument("--force", action="store_true", help="Regenerate from example")
    prep.add_argument("--new-secret", action="store_true", help="Generate a new SECRET_KEY")
    prep.set_defaults(func=cmd_prepare)

    migrate = sub.add_parser("migrate", parents=[common], help="Run Alembic migrations")
    migrate.add_argument("--revision", default="head")
    migrate.add_argument("--ensure-env", action="store_true", help="Create env file if missing")
    migrate.set_defaults(func=cmd_migrate)

    start = sub.add_parser("start", parents=[common], help="Launch uvicorn")
    start.add_argument("--host", default="0.0.0.0")
    start.add_argument("--port", type=int, default=8000)
    start.add_argument("--workers", type=int)
    start.add_argument("--reload", action="store_true")
    start.add_argument("--ensure-env", action="store_true")
    start.set_defaults(func=cmd_start)

    deploy = sub.add_parser("deploy", parents=[common], help="Prepare env, migrate, and start server")
    deploy.add_argument("--revision", default="head")
    deploy.add_argument("--host", default="0.0.0.0")
    deploy.add_argument("--port", type=int, default=8000)
    deploy.add_argument("--workers", type=int)
    deploy.add_argument("--reload", action="store_true")
    deploy.add_argument("--ensure-env", action="store_true")
    deploy.add_argument("--skip-migrate", action="store_true")
    deploy.add_argument("--skip-start", action="store_true")
    deploy.add_argument("--force-start", action="store_true", help="Start server even in CI")
    deploy.set_defaults(func=cmd_deploy)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
