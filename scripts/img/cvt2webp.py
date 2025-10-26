import os
import asyncio
from PIL import Image


QUALITY = 50

accepted_ext = ['.png', '.jpg', '.HEIC', '.HEIF']


def get_img_files(path='.'):
    files = []
    for filename in os.listdir(path):
        # if filename.endswith('.png') or filename.endswith('.jpg') or filename.endswith('.HEIC'):
        if any(filename.endswith(ext) for ext in accepted_ext):
            files.append(os.path.join(path, filename))
    return files


async def convert(file):
    image = Image.open(file)
    # image = image.convert('RGB')
    # new_file = file.replace('.png', '.webp').replace('.jpg', '.webp').replace('.HEIC', '.webp')
    # new_file = file
    for ext in accepted_ext:
        if file.endswith(ext):
            new_file = file.replace(ext, '.webp')
            break
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

    if any(img_file.endswith('.HEIC') for img_file in img_files) or any(img_file.endswith('.HEIF') for img_file in img_files):
        # from pillow_heif import register_heif_opener
        # pi_heif is a light version of Pillow-Heif ... includes only HEIF decoder and does not support save operations.
        from pi_heif import register_heif_opener

        register_heif_opener()

    to_webp_tasks = []
    for img_file in img_files:
        to_webp_tasks.append(convert(img_file))
    # loop = asyncio.get_event_loop()
    # loop.run_until_complete(asyncio.wait(tasks))
    # loop.close()
    asyncio.run(runner(to_webp_tasks))
