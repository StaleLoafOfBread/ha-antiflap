"""Validate that a PR manifest version is greater than the base branch version."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from packaging.version import InvalidVersion, Version


def error(
    manifest_path: Path,
    title: str,
    message: str,
    *,
    line: int | None = None,
    col: int | None = None,
) -> None:
    """Emit a GitHub Actions error annotation."""
    location = f"file={manifest_path}"
    if line is not None:
        location += f",line={line}"
    if col is not None:
        location += f",col={col}"

    print(f"::error {location},title={title}::{message}", file=sys.stderr)


def read_manifest_version(manifest_path: Path, label: str, content: str) -> str:
    """Read the version string from manifest JSON content."""
    try:
        manifest = json.loads(content)
    except json.JSONDecodeError as err:
        error(
            manifest_path,
            f"Invalid {label} JSON",
            f"Could not parse manifest.json: {err.msg}",
            line=err.lineno,
            col=err.colno,
        )
        raise

    try:
        version = manifest["version"]
    except KeyError:
        error(
            manifest_path,
            f"Missing {label} version",
            "manifest.json must contain a version field",
        )
        raise

    if not isinstance(version, str):
        error(
            manifest_path,
            f"Invalid {label} version",
            "manifest.json version must be a string",
        )
        raise TypeError("manifest version must be a string")

    return version


def read_base_manifest(manifest_path: Path) -> str:
    """Read manifest content from FETCH_HEAD."""
    try:
        return subprocess.check_output(
            ["git", "show", f"FETCH_HEAD:{manifest_path}"],
            text=True,
        )
    except subprocess.CalledProcessError:
        error(
            manifest_path,
            "Missing base branch manifest",
            "Could not read manifest.json from the pull request base branch",
        )
        raise


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--manifest-path", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    """Validate the pull request manifest version."""
    args = parse_args()
    manifest_path: Path = args.manifest_path

    try:
        base_manifest = read_base_manifest(manifest_path)
        pr_manifest = manifest_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        error(manifest_path, "Missing pull request manifest", "manifest.json was not found")
        return 1
    except subprocess.CalledProcessError:
        return 1

    try:
        base_version = read_manifest_version(
            manifest_path,
            "base branch manifest",
            base_manifest,
        )
        pr_version = read_manifest_version(
            manifest_path,
            "pull request manifest",
            pr_manifest,
        )
        base_parsed = Version(base_version)
        pr_parsed = Version(pr_version)
    except (json.JSONDecodeError, KeyError, TypeError):
        return 1
    except InvalidVersion as err:
        error(manifest_path, "Invalid manifest version", str(err))
        return 1

    print(f"Base branch: {args.base_ref}")
    print(f"Base manifest version: {base_version}")
    print(f"Pull request manifest version: {pr_version}")

    if pr_parsed <= base_parsed:
        error(
            manifest_path,
            "Manifest version not increased",
            f"{pr_version} <= {base_version}",
        )
        return 1

    print(f"Manifest version increased: {base_version} -> {pr_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
