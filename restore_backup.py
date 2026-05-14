from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
LOGGER = logging.getLogger(__name__)


def load_config(config_path: Path) -> Mapping[str, Any]:
    """Load the backup configuration from a TOML file.

    Args:
        config_path: Path to the config.toml file.

    Returns:
        The backup service configuration dictionary.
    """
    if not config_path.exists():
        LOGGER.error("Config file not found: %s", config_path)
        sys.exit(1)

    with config_path.open("rb") as f:
        data = tomllib.load(f)

    backup_config = data.get("services", {}).get("backup")
    if not backup_config:
        LOGGER.error("Section [services.backup] not found in %s", config_path)
        sys.exit(1)

    return backup_config


def run_rclone(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run an rclone command and return the result.

    Args:
        cmd: List of command arguments.

    Returns:
        The execution result.
    """
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        LOGGER.error("Rclone command failed: %s", e.stderr.strip())
        sys.exit(1)
    except FileNotFoundError:
        LOGGER.error("Rclone binary not found. Is it in your PATH?")
        sys.exit(1)


def list_backups(config: Mapping[str, Any]) -> None:
    """List all available backups on the remote.

    Args:
        config: Backup service configuration.
    """
    rclone = config.get("rclone_binary", "rclone")
    remote_name = config.get("remote_name", "")
    remote_root = config.get("remote_root", "backups")

    remote_base = f"{remote_name}:{remote_root}" if remote_name else remote_root

    LOGGER.info("Listing backups in %s...", remote_base)
    result = run_rclone([rclone, "lsf", "--dirs-only", remote_base])

    folders = sorted(result.stdout.splitlines(), reverse=True)
    if not folders:
        print("No backups found.")
        return

    print("\nAvailable Backups:")
    for folder in folders:
        print(f"  - {folder.strip('/')}")


def restore_backup(
    config: Mapping[str, Any], timestamp: str, force: bool = False
) -> None:
    """Restore a specific backup using its index.json.

    Args:
        config: Backup service configuration.
        timestamp: The backup folder ID to restore.
        force: Whether to skip the confirmation prompt.
    """
    rclone = config.get("rclone_binary", "rclone")
    remote_name = config.get("remote_name", "")
    remote_root = config.get("remote_root", "backups")

    remote_prefix = f"{remote_name}:{remote_root}" if remote_name else remote_root
    backup_folder = f"{remote_prefix}/{timestamp}"

    LOGGER.info("Fetching index from %s...", backup_folder)
    index_res = run_rclone([rclone, "cat", f"{backup_folder}/index.json"])

    try:
        index_data = json.loads(index_res.stdout)
    except json.JSONDecodeError:
        LOGGER.error("Failed to parse index.json for backup %s", timestamp)
        sys.exit(1)

    items = index_data.get("items", [])
    if not items:
        LOGGER.warning("No items found in backup index.")
        return

    print(f"\nRestore Plan for backup {timestamp}:")
    for item in items:
        print(f"  {item['output_subpath']} -> {item['input_path']}")

    if not force:
        confirm = input("\nProceed with restore? (y/N): ")
        if confirm.lower() != "y":
            print("Restore cancelled.")
            return

    for item in items:
        source = f"{backup_folder}/{item['output_subpath']}"
        dest = Path(item["input_path"])

        # Ensure parent directory exists
        dest.parent.mkdir(parents=True, exist_ok=True)

        if item.get("is_file") and item.get("name"):
            # If it's a file, we want to copy the specific file back
            file_source = f"{source}/{item['name']}"
            LOGGER.info("Restoring file %s...", dest)
            run_rclone([rclone, "copyto", file_source, str(dest)])
        else:
            LOGGER.info("Restoring directory %s...", dest)
            run_rclone([rclone, "copy", source, str(dest)])

    print("\nRestore completed successfully.")


def main() -> None:
    """Main entry point for the restore utility."""
    parser = argparse.ArgumentParser(description="Restore utility for BackupService")
    parser.add_argument(
        "--config", type=Path, default=Path("config.toml"), help="Path to config.toml"
    )
    parser.add_argument("--list", action="store_true", help="List available backups")
    parser.add_argument("--timestamp", help="Timestamp/folder name to restore")
    parser.add_argument("--force", action="store_true", help="Skip confirmation prompt")

    args = parser.parse_args()
    config = load_config(args.config)

    if args.list:
        list_backups(config)
    elif args.timestamp:
        restore_backup(config, args.timestamp, args.force)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
