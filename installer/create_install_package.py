"""Build install materials with an empty database state.

This script creates a distributable archive under dist/ that contains
runtime code and installer scripts, while excluding local runtime data.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = PROJECT_ROOT / 'dist'

# Keep the package scope explicit to avoid shipping local artifacts.
INCLUDE_PATHS = [
    'INSTALL_GUIDE.txt',
    'cli',
    'core',
    'docs',
    'installer',
    'requirements.txt',
    'scripts',
    'services',
    'templates', 
    'web',
]

EXCLUDE_DIRS = {
    '__pycache__',
    '.venv',
    '.vscode',
    'dist',
}

EXCLUDE_SUFFIXES = {
    '.pyc',
    '.pyo',
}


def _copy_with_ps1_utf8_bom(src: str, dst: str) -> str:
    src_path = Path(src)
    dst_path = Path(dst)

    if src_path.suffix.lower() == '.ps1':
        text = src_path.read_text(encoding='utf-8')
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        dst_path.write_text(text, encoding='utf-8-sig')
        shutil.copystat(src_path, dst_path)
        return str(dst_path)

    return shutil.copy2(src, dst)

def _ignore_filter(_: str, names: list[str]) -> set[str]:
    ignored = set()
    for name in names:
        if name in EXCLUDE_DIRS:
            ignored.add(name)
            continue
        path = Path(name)
        if path.suffix in EXCLUDE_SUFFIXES:
            ignored.add(name)
    return ignored


def build_package() -> Path:
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    package_root = DIST_DIR / f'vm-entry-install-{timestamp}'

    if package_root.exists():
        shutil.rmtree(package_root)
    package_root.mkdir(parents=True, exist_ok=True)

    for relative in INCLUDE_PATHS:
        src = PROJECT_ROOT / relative
        dst = package_root / relative
        if src.is_dir():
            shutil.copytree(
                src,
                dst,
                ignore=_ignore_filter,
                copy_function=_copy_with_ps1_utf8_bom,
            )
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            _copy_with_ps1_utf8_bom(str(src), str(dst))

    data_dir = package_root / 'data'
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / '.gitkeep').write_text('', encoding='utf-8')

    db_file = data_dir / 'vm_entry.db'
    if db_file.exists():
        raise RuntimeError('Package contains vm_entry.db unexpectedly.')

    archive_base = DIST_DIR / f'{package_root.name}'
    archive_path = Path(shutil.make_archive(str(archive_base), 'zip', root_dir=package_root.parent, base_dir=package_root.name))
    return archive_path


def main() -> None:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = build_package()
    print(f'Install package created: {archive_path}')


if __name__ == '__main__':
    main()
