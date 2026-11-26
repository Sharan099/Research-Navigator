"""Run reproducible local release checks; remote CI and deployment remain separate gates."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--audit", action="store_true", help="Query public vulnerability registries")
    parser.add_argument("--database", type=Path, help="Also audit this SQLite database read-only")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    node = shutil.which("node")
    if not npm or not node:
        parser.exit(1, "Node.js 22 and npm are required.\n")
    commands = [
        [sys.executable, "-m", "ruff", "check", "backend", "tests", "scripts", "migrations"],
        [sys.executable, "-m", "pytest", "-q", "--cov", "--cov-report=term-missing", "--cov-report=xml"],
        [sys.executable, "scripts/test_operations.py"],
        [node, "--check", "frontend/app.js"],
        [node, "--test", "tests/frontend.test.cjs"],
    ]
    if args.browser:
        commands.append([npm, "run", "test:browser"])
        commands.append([npm, "run", "test:browser:public"])
    if args.audit:
        commands.extend(
            [
                [sys.executable, "-m", "pip_audit", "--no-deps", "--disable-pip", "-r", "requirements.lock"],
                [npm, "audit", "--audit-level=high", "--cache", ".cache/npm"],
            ]
        )
    if args.database:
        commands.append([sys.executable, "scripts/evaluate.py", "--database", str(args.database), "--benchmark"])
    for command in commands:
        print("Running " + " ".join(command[1:]), flush=True)
        completed = subprocess.run(command, cwd=root, check=False)
        if completed.returncode:
            return completed.returncode
    print("Selected local gates passed. Confirm PostgreSQL, container and remote CI evidence separately.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
