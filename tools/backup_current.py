"""Create an independent, verified stable copy without hard-linking files."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import sys
import time

SOURCE = Path(__file__).resolve().parents[1]


def sha256(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def copy_verified(source, target):
    before = source.stat()
    shutil.copy2(source, target)
    source_hash = sha256(source)
    target_hash = sha256(target)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f'Source changed during backup: {source}')
    if source_hash != target_hash:
        raise RuntimeError(f'Backup checksum mismatch: {source}')
    return {'path': str(source.relative_to(SOURCE)), 'bytes': before.st_size,
            'source_sha256': source_hash, 'backup_sha256': target_hash}


def create_backup(destination):
    destination = destination.resolve()
    if destination == SOURCE or destination.is_relative_to(SOURCE):
        raise ValueError('The stable copy must be outside the working directory.')
    if destination.exists():
        raise FileExistsError(f'Backup destination already exists: {destination}')
    started = time.monotonic()
    entries, directories, skipped_links = [], [], []
    for folder, dir_names, file_names in os.walk(SOURCE, followlinks=False):
        folder = Path(folder)
        real_dirs = []
        for name in dir_names:
            child = folder/name
            # Test scratch folders contain generated Windows directory symlinks.
            if child.is_symlink() or bool(child.stat(follow_symlinks=False).st_file_attributes & 0x400):
                skipped_links.append(str(child.relative_to(SOURCE)))
            else:
                real_dirs.append(name)
        dir_names[:] = real_dirs
        directories.append(folder.relative_to(SOURCE))
        for name in file_names:
            child = folder/name
            if child.is_symlink():
                skipped_links.append(str(child.relative_to(SOURCE)))
            else:
                entries.append(child)
    total_bytes = sum(path.stat().st_size for path in entries)
    if shutil.disk_usage(destination.parent).free < total_bytes + 1024**3:
        raise RuntimeError('Insufficient free space for an independent backup plus 1 GiB headroom.')
    destination.mkdir()
    for relative in directories:
        (destination/relative).mkdir(exist_ok=True)
    print(f'Copying and SHA256-verifying {len(entries):,} files ({total_bytes/1024**3:.2f} GiB).', flush=True)
    records = []
    last_progress = time.monotonic()
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(copy_verified, path, destination/path.relative_to(SOURCE)) for path in entries]
        for future in as_completed(futures):
            records.append(future.result())
            if time.monotonic()-last_progress >= 8:
                print(f'Verified {len(records):,}/{len(entries):,} files.', flush=True)
                last_progress = time.monotonic()

    adaptations = []

    def relocate(relative):
        path = destination/relative
        try:
            contents = path.read_text(encoding='utf-8')
        except (UnicodeError, OSError):
            return
        updated = contents.replace(str(SOURCE), str(destination)).replace(SOURCE.as_posix(), destination.as_posix())
        if updated != contents:
            path.write_text(updated, encoding='utf-8')
            adaptations.append({'path': str(relative), 'reason': 'Point the copied environment at its own runtime.'})

    relocate(Path('.venv/pyvenv.cfg'))
    for path in (destination/'.venv'/'Scripts').iterdir():
        if path.is_file() and (path.name.lower().startswith('activate') or path.suffix.lower() in ('.ps1', '.bat', '.cmd', '.nu', '.sh', '.fish', '.csh')):
            relocate(path.relative_to(destination))

    # Use a distinct preference namespace so changing the working application's
    # cache/output preferences cannot affect the preserved stable version.
    app_path = destination/'watermark_remover'/'app.py'
    contents = app_path.read_text(encoding='utf-8')
    contents = contents.replace("QSettings('Clearframe', 'VideoRepair')", "QSettings('Clearframe', 'VideoRepair-Stable-1.1.1')")
    contents = contents.replace("self.setWindowTitle('Clearframe · Video Watermark Remover')", "self.setWindowTitle('Clearframe Stable 1.1.1 · Video Watermark Remover')")
    app_path.write_text(contents, encoding='utf-8')
    adaptations.append({'path': 'watermark_remover/app.py', 'reason': 'Separate stable-version preferences and window title; repair behavior is unchanged.'})
    adapted_paths = {Path(record['path']) for record in adaptations}
    for record in records:
        if Path(record['path']) in adapted_paths:
            record['backup_sha256'] = sha256(destination/record['path'])
    records.sort(key=lambda record: record['path'])
    manifest = {'version': '1.1.1', 'created_utc': datetime.now(timezone.utc).isoformat(),
                'source': str(SOURCE), 'backup': str(destination), 'file_count': len(records),
                'total_bytes': total_bytes, 'copy_and_verify_seconds': round(time.monotonic()-started, 2),
                'independent_file_copies': True, 'skipped_generated_links': skipped_links,
                'relocation_adaptations': adaptations, 'files': records}
    (destination/'BACKUP_MANIFEST.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    (destination/'STABLE_VERSION.md').write_text(
        '# Clearframe Stable 1.1.1\n\n'
        'This is the preserved working version for simpler watermark projects, created on 12 November 2026.\n\n'
        'Start Clearframe.exe from this folder. Its Python runtime, installed dependencies, model weights, '
        'source code, and samples were copied independently and checksum-verified. The environment paths '
        'were relocated here, and this copy has its own preferences and a distinct stable-version window title.\n\n'
        f'Continue future development in: {SOURCE}\n\n'
        'Keep this stable folder at its present location. The copied environment has absolute runtime paths. '
        'BACKUP_MANIFEST.json records file checksums and the relocation adjustments. '
        'Historical job records retain their original source/output paths.\n', encoding='utf-8')
    print(f'BACKUP_READY {destination}', flush=True)
    print(f'Copied and verified {len(records):,} files in {time.monotonic()-started:.1f} seconds.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    create_backup(parser.parse_args().destination)
