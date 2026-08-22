#!/usr/bin/env python3
"""Convert photos to webp, named after the time they were taken.

Run it inside a directory of source images (the usual case), or point it
somewhere with --dir:

    cd posts/260531-hk-trip/raw && uv run ../../../scripts/img/cvt2webp.py
    uv run scripts/img/cvt2webp.py --dir posts/260531-hk-trip/raw

Sources are left in place; only the .webp files are new.
"""

import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

from PIL import Image, ImageOps, ExifTags

QUALITY = 50
# Longest edge in pixels, 0 to keep the original size. The site itself never
# serves anything wider than 1600, so anything above ~2560 is dead weight.
MAX_SIZE = 0

accepted_ext = ['.png', '.jpg', '.jpeg', '.HEIC', '.HEIF']
EXIF_DATETIME_TAGS = (
    'DateTimeOriginal',
    'DateTimeDigitized',
    'DateTime',
)
HEIF_EXT = ('.heic', '.heif')


def register_heif():
    # pi_heif is a light version of Pillow-Heif: decoder only, no save support.
    from pi_heif import register_heif_opener
    register_heif_opener()


def get_img_files(path='.'):
    files = []
    for filename in sorted(os.listdir(path)):
        if any(filename.lower().endswith(ext.lower()) for ext in accepted_ext):
            files.append(os.path.join(path, filename))
    return files


def get_exif_datetime(image):
    try:
        exif = image.getexif()
        if not exif:
            return None

        tag_map = {
            ExifTags.TAGS.get(k): v
            for k, v in exif.items()
            if ExifTags.TAGS.get(k) in EXIF_DATETIME_TAGS
        }
        for tag in EXIF_DATETIME_TAGS:
            if tag in tag_map:
                return datetime.strptime(tag_map[tag], '%Y:%m:%d %H:%M:%S')
    except Exception:
        pass
    return None


def unique_path(path, taken):
    """A free output path, avoiding names already claimed in this run."""
    base, ext = os.path.splitext(path)
    i = 1
    while path in taken or os.path.exists(path):
        path = f'{base}_{i}{ext}'
        i += 1
    return path


def plan(files):
    """Decide every output name up front, in one process.

    Naming has to be sequential anyway (two photos taken in the same second
    would otherwise race for the same name), and reading EXIF is cheap because
    Pillow does not decode pixels for it.
    """
    tasks = []
    taken = set()
    for file in files:
        if file.lower().endswith(HEIF_EXT):
            register_heif()
        with Image.open(file) as image:
            dt = get_exif_datetime(image)
        name = (dt.strftime('IMG_%Y%m%d_%H%M%S') if dt
                else os.path.splitext(os.path.basename(file))[0])
        new_file = unique_path(
            os.path.join(os.path.dirname(file), f'{name}.webp'), taken)
        taken.add(new_file)
        tasks.append((file, new_file))
    return tasks


def convert(task, quality=QUALITY, max_size=MAX_SIZE):
    file, new_file = task
    if file.lower().endswith(HEIF_EXT):
        register_heif()
    with Image.open(file) as image:
        # Phone cameras record orientation in EXIF rather than rotating the
        # pixels. Pillow drops EXIF when saving webp, so without this the
        # image ends up sideways on the site.
        image = ImageOps.exif_transpose(image)
        if max_size:
            image.thumbnail((max_size, max_size), Image.LANCZOS)
        image.save(
            new_file,  # noqa: must match
            'webp',
            quality=quality,
            method=6,  # slowest, smallest; parallelism pays for it
        )
    return file, new_file, os.path.getsize(new_file)


def _worker(args):
    return convert(*args)


def human(n):
    for unit in ('B', 'KB', 'MB'):
        if abs(n) < 1024 or unit == 'MB':
            return f'{n:.0f}{unit}' if unit == 'B' else f'{n:.1f}{unit}'
        n /= 1024


def main():
    parser = argparse.ArgumentParser(
        description='Convert images in a directory to webp.')
    parser.add_argument('--dir', default='.', help='source directory')
    parser.add_argument('--quality', type=int, default=QUALITY)
    parser.add_argument('--max-size', type=int, default=MAX_SIZE,
                        help='longest edge in pixels, 0 to keep original')
    parser.add_argument('--workers', type=int, default=os.cpu_count(),
                        help='parallel encoders')
    args = parser.parse_args()

    img_files = get_img_files(args.dir)
    if not img_files:
        print('nothing to convert')
        return 0

    tasks = plan(img_files)
    before = sum(os.path.getsize(f) for f, _ in tasks)

    payload = [(t, args.quality, args.max_size) for t in tasks]
    after = 0
    # Encoding is CPU-bound C code, so processes are what actually parallelise
    # it; threads or coroutines would run these back to back on one core.
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for src, dst, size in pool.map(_worker, payload):
            after += size
            print(f'Converted {os.path.basename(src)} -> '
                  f'{os.path.basename(dst)} ({human(size)})')

    print(f'\n{len(tasks)} image(s): {human(before)} -> {human(after)} '
          f'({after / before:.0%})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
