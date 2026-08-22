# download external media

import os
import re
import logging
import requests
from tqdm import tqdm
from data import posts, pwd
from media import get_content_type


# media in markdown: ![description](link)
media_regex = r'!\[.*\]\((.*)\)'
media_pattern = re.compile(media_regex)

url_regex = r'(https?):\/\/([\w_-]+(?:(?:\.[\w_-]+)+))([\w.,@?^=%&:\/~+#-]*[\w@?^=%&\/~+#-])'
url_pattern = re.compile(url_regex)


# https://gist.github.com/yanqd0/c13ed29e29432e3cf3e7c38467f42f51
def download(url: str, chunk_size: int = 1024 * 128) -> bytes:
    r = requests.get(url, stream=True)
    content = b''
    total = int(r.headers.get('content-length', 0))
    with tqdm(
        desc='  DL: ' + '{:<32}'.format(url.split('/')[-1].split('#')[0][:32]),
        total=total,
        unit='iB',
        unit_scale=True,
        unit_divisor=1024,
    ) as bar:
        for data in r.iter_content(chunk_size=chunk_size):
            content += data
            size = len(data)
            bar.update(size)
    return content


def dl_ext_media(post_name):
    post_file = os.path.join(pwd, 'posts', post_name, 'index.md')
    ext_media_path = os.path.join(pwd, 'posts', post_name, 'ext')

    media_md = []
    with open(post_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    for line in lines:
        if result := media_pattern.search(line):
            media_md.append(result.group(1))

    # check if media is external
    edited = False
    media_id = 1
    pbar = tqdm(media_md)
    for media in pbar:
        pbar.set_description(f'[media]\t{media}')
        if url_pattern.search(media):
            edited = True
            # download media
            media_name = media_id

            media_url = media
            media_type = get_content_type(media_url)
            media_ext = media_type.split('/')[1]
            if media_ext == 'jpeg':
                media_ext = 'jpg'

            os.makedirs(ext_media_path, exist_ok=True)
            media_filename = f'{media_name:02d}.{media_ext}'
            with open(os.path.join(ext_media_path, media_filename), 'wb') as f:
                # f.write(download(media_url))
                f.write(requests.get(media_url).content)

            # replace media url
            for i, line in enumerate(lines):
                if media_url in line:
                    lines[i] = line.replace(media_url, f'ext/{media_filename} "{media_url}"')

            media_id += 1
            # logging.info(f'[media]\texternal {media_url} downloaded')

    if edited:
        with open(post_file, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        return logging.info(f'[media]\tpost {post_name} media replaced')
    else:
        return None


if __name__ == '__main__':
    for info in posts.values():
        for post_dir in info['dirs']:
            dl_ext_media(post_dir)
