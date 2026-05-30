import os
import asyncio
from datetime import datetime
from PIL import Image, ExifTags


QUALITY = 50

accepted_ext = ['.png', '.jpg', '.HEIC', '.HEIF']
EXIF_DATETIME_TAGS = {
    'DateTimeOriginal',
    'DateTimeDigitized',
    'DateTime',
}

def get_img_files(path='.'):
    files = []
    for filename in os.listdir(path):
        # if filename.endswith('.png') or filename.endswith('.jpg') ...
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
                return datetime.strptime(tag_map[tag], "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    return None


def unique_path(path):
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists(path):
        path = f"{base}_{i}{ext}"
        i += 1
    return path


async def convert(file):
    image = Image.open(file)
    # image = image.convert('RGB')

    dt = get_exif_datetime(image)
    if dt:
        name = dt.strftime("IMG_%Y%m%d_%H%M%S")
    else:
        name = os.path.splitext(os.path.basename(file))[0]

    new_file = os.path.join(
        os.path.dirname(file),
        f"{name}.webp"
    )
    new_file = unique_path(new_file)

    image.save(
        new_file,  # noqa: must match
        'webp',
        optimize=True,
        quality=QUALITY
    )

    # sleep for 10 seconds before removing the file
    # await asyncio.sleep(10)
    # os.remove(file)
    return print('Converted {} to {}'.format(file, new_file))


async def runner(tasks):
    return await asyncio.gather(*tasks)


# Convert png to jpg and slim it
if __name__ == '__main__':
    img_files = get_img_files()
    if not img_files:
        exit(0)

    if any(f.upper().endswith(('.HEIC', '.HEIF')) for f in img_files):
        # from pillow_heif import register_heif_opener
        # pi_heif is a light version of Pillow-Heif ... includes only HEIF decoder and does not support save operations.
        from pi_heif import register_heif_opener
        register_heif_opener()

    asyncio.run(runner([convert(f) for f in img_files]))
