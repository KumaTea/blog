import os
import asyncio
from PIL import Image


QUALITY = 50


def get_img_files(path='.'):
    files = []
    for filename in os.listdir(path):
        if filename.endswith('.png') or filename.endswith('.jpg'):
            files.append(os.path.join(path, filename))
    return files


async def convert(file):
    image = Image.open(file)
    # image = image.convert('RGB')
    new_file = file.replace('.png', '.webp').replace('.jpg', '.webp')
    image.save(
        new_file,
        'webp',
        optimize=True,
        quality=QUALITY
    )

    # sleep for 10 seconds before removeing the file
    # await asyncio.sleep(10)
    # os.remove(file)
    return print('Converted {} to {}'.format(file, new_file))


async def runner(tasks):
    return await asyncio.gather(*tasks)


# Convert png to jpg and slim it
if __name__ == '__main__':
    files = get_img_files()
    if not files:
        exit(0)
    tasks = []
    for file in files:
        tasks.append(convert(file))
    # loop = asyncio.get_event_loop()
    # loop.run_until_complete(asyncio.wait(tasks))
    # loop.close()
    asyncio.run(runner(tasks))
