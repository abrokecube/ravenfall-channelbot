import os
from datetime import datetime
import shutil
import aiofiles

def backup_file_with_date(filepath, max_backups=5):
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"The file '{filepath}' does not exist.")

    base_dir = os.path.dirname(filepath)
    filename = os.path.basename(filepath)
    name, ext = os.path.splitext(filename)

    # Create backup directory
    backup_dir = os.path.join(base_dir, "backup")
    os.makedirs(backup_dir, exist_ok=True)

    # Create new backup filename
    date_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    new_filename = f"{name}_{date_str}{ext}"
    new_filepath = os.path.join(backup_dir, new_filename)

    # Copy the file
    shutil.copy2(filepath, new_filepath)

    # Find all existing backups for this file
    backups = []
    for file in os.listdir(backup_dir):
        if file.startswith(name + "_") and file.endswith(ext):
            full_path = os.path.join(backup_dir, file)
            if os.path.isfile(full_path):
                backups.append((full_path, os.path.getctime(full_path)))

    # Sort by creation time (oldest first)
    backups.sort(key=lambda x: x[1])

    # Remove oldest if exceeding max_backups
    while len(backups) > max_backups:
        oldest_file = backups.pop(0)[0]
        os.remove(oldest_file)

    return new_filepath

async def backup_file_with_date_async(filepath, max_backups=5):
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"The file '{filepath}' does not exist.")

    base_dir = os.path.dirname(filepath)
    filename = os.path.basename(filepath)
    name, ext = os.path.splitext(filename)

    # Create backup directory
    backup_dir = os.path.join(base_dir, "backup")
    os.makedirs(backup_dir, exist_ok=True)

    # Create new backup filename
    date_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    new_filename = f"{name}_{date_str}{ext}"
    new_filepath = os.path.join(backup_dir, new_filename)

    # Copy the file
    async with aiofiles.open(filepath, 'rb') as f:
        async with aiofiles.open(new_filepath, 'wb') as new_f:
            await new_f.write(await f.read())

    # Find all existing backups for this file
    backups = []
    for file in os.listdir(backup_dir):
        if file.startswith(name + "_") and file.endswith(ext):
            full_path = os.path.join(backup_dir, file)
            if os.path.isfile(full_path):
                backups.append((full_path, os.path.getctime(full_path)))

    # Sort by creation time (oldest first)
    backups.sort(key=lambda x: x[1])

    # Remove oldest if exceeding max_backups
    while len(backups) > max_backups:
        oldest_file = backups.pop(0)[0]
        os.remove(oldest_file)

    return new_filepath
