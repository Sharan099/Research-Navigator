"""Export an allowlisted, credential-checked source checkout without local research data."""

import json
import os
import shutil
import tempfile
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    "README.md",
    "LICENSE",
    "Dockerfile",
    ".dockerignore",
    ".gitignore",
    ".env.example",
    "pyproject.toml",
    "alembic.ini",
    "requirements.lock",
    "requirements-dev.lock",
    "compose.yaml",
    "package.json",
    "package-lock.json",
    "playwright.config.cjs",
    "playwright.public.config.cjs",
    "CODE_MAP.md",
    "LEARNING_GUIDE.md",
    "SOURCE_REGISTRY.md",
    "AGENTS.md",
}
SOURCE_DIRS = {"backend", "frontend", "migrations", "tests", "scripts", "docs", ".github"}
EXTENSIONS = {".py", ".js", ".cjs", ".css", ".html", ".md", ".toml", ".ini", ".yml", ".yaml"}


def release_files(root=None):
    root = root or ROOT
    for name in sorted(ROOT_FILES):
        path = root / name
        if path.is_file():
            yield path
    for directory in sorted(SOURCE_DIRS):
        for path in sorted((root / directory).rglob("*")):
            if path.is_symlink() or "__pycache__" in path.parts:
                continue
            if path.is_file() and path.suffix in EXTENSIONS:
                yield path
    if (root / "evaluation/README.md").is_file():
        yield root / "evaluation/README.md"
    if (root / "evaluation/HUMAN_REVIEW.md").is_file():
        yield root / "evaluation/HUMAN_REVIEW.md"
    for name in ("docs/media/research-navigator-demo.mp4", "docs/media/demo-poster.jpg"):
        if (root / name).is_file():
            yield root / name
    for path in sorted((root / "evaluation/published").glob("*")):
        if path.is_file() and not path.is_symlink() and path.suffix in {".json", ".md"}:
            yield path


def export_release():
    paths = list(release_files())
    secrets = [
        value
        for name, value in [*dotenv_values(ROOT / ".env").items(), *os.environ.items()]
        if value and len(value) >= 12 and any(word in name.upper() for word in ("KEY", "TOKEN", "SECRET"))
    ]
    try:
        from huggingface_hub import get_token

        if get_token():
            secrets.append(get_token())
    except ImportError:
        pass
    for path in paths:
        content = path.read_bytes()
        if any(secret.encode() in content for secret in secrets):
            raise ValueError("Credential scan failed in " + str(path.relative_to(ROOT)))
    destination_root = ROOT / ".runtime"
    destination_root.mkdir(exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix="release-", dir=destination_root))
    for path in paths:
        target = destination / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    return {"directory": str(destination), "files": len(paths), "credential_value_scan": "passed"}


if __name__ == "__main__":
    print(json.dumps(export_release(), indent=2))
