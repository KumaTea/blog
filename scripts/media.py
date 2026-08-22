import os
import re
import random
import string
import shutil
import logging
import zipfile
import tempfile
import requests
import urllib.request
from data import posts, pwd, repo


logging.basicConfig(level=logging.INFO)

media_file = 'media.zip'
download_url = 'https://github.com/{repo}/releases/download/{tag}/{name}'


def get_media(post_id, temp_path):
    """Download one post's media.zip from its release."""
    url = download_url.format(repo=repo, tag=post_id, name=media_file)
    temp_media_path = os.path.join(temp_path, post_id, media_file)
    os.makedirs(os.path.dirname(temp_media_path), exist_ok=True)

    r = requests.get(url, stream=True, timeout=120)
    # Without this a 404 quietly writes GitHub's error page into media.zip and
    # the build carries on with missing images.
    r.raise_for_status()
    with open(temp_media_path, 'wb') as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)

    size = os.path.getsize(temp_media_path)
    logging.info(f'[media]\tpost {post_id} downloaded ({size} bytes)')
    return temp_media_path


def unzip_media(post_id, archive, post_dirs):
    """Extract the archive into every post directory fed by this tag."""
    with zipfile.ZipFile(archive) as zf:
        bad = zf.testzip()
        if bad is not None:
            raise RuntimeError(f'[media]\t{post_id} archive corrupt at {bad}')
        members = [i for i in zf.infolist() if not i.is_dir()]
        for post_dir in post_dirs:
            post_path = os.path.join(pwd, 'posts', post_dir)
            os.makedirs(post_path, exist_ok=True)
            for info in members:
                # Some archives were written on Windows and use backslashes.
                # unzip(1) normalises those; zipfile would instead create one
                # file literally named "img\cover.jpg".
                name = info.filename.replace('\\', '/')
                target = os.path.normpath(os.path.join(post_path, name))
                if not target.startswith(os.path.normpath(post_path) + os.sep):
                    raise RuntimeError(f'[media]\t{post_id} path escape: {name}')
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(info) as src, open(target, 'wb') as dst:
                    shutil.copyfileobj(src, dst)
            logging.info(f'[media]\t{post_dir} unzipped '
                         f'({len(members)} files)')


def gen_uuid(length=4):
    # https://stackoverflow.com/a/56398787/10714490
    alphabet = string.ascii_lowercase + string.digits
    return ''.join(random.choices(alphabet, k=length))


def get_content_type(url):
    with urllib.request.urlopen(url) as response:
        return response.headers['content-type']


def get_external_media(post_dir):
    used_uuid = []
    post_file = os.path.join(pwd, 'posts', post_dir, 'index.md')
    media_path = os.path.join(pwd, 'posts', post_dir, 'img')
    os.makedirs(os.path.dirname(media_path), exist_ok=True)

    # media in markdown: ![description](link)
    media_regex = r'!\[.*\]\((.*)\)'
    media_md = []
    with open(post_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    for line in lines:
        if re.search(media_regex, line):
            media_md.append(re.search(media_regex, line).group(1))

    # check if media is external
    url_regex = r'(https?):\/\/([\w_-]+(?:(?:\.[\w_-]+)+))([\w.,@?^=%&:\/~+#-]*[\w@?^=%&\/~+#-])'
    edited = False
    for media in media_md:
        if re.search(url_regex, media):
            edited = True
            # download media
            media_name = gen_uuid()
            while media_name in used_uuid:
                media_name = gen_uuid()
            used_uuid.append(media_name)

            media_url = media
            media_type = get_content_type(media_url)
            media_ext = media_type.split('/')[1]
            if media_ext == 'jpeg':
                media_ext = 'jpg'

            media_filename = f'{media_name}.{media_ext}'
            with open(os.path.join(media_path, media_filename), 'wb') as f:
                r = requests.get(media_url)
                f.write(r.content)

            # replace media url
            for i, line in enumerate(lines):
                if media in line:
                    lines[i] = line.replace(media, f'img/{media_filename}')
            logging.info(f'[media]\texternal {media} downloaded')

    if edited:
        with open(post_file, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        return logging.info(f'[media]\tpost {post_dir} media replaced')
    else:
        return None


if __name__ == '__main__':
    if not posts:
        raise SystemExit('[media]\tno tagged posts found, refusing to build '
                         'a site with no images')

    temp_path = tempfile.mkdtemp(prefix='blog-media-')
    logging.info(f'[media]\tusing temp directory {temp_path}')
    try:
        for pid, info in sorted(posts.items()):
            archive = get_media(pid, temp_path)
            unzip_media(pid, archive, info['dirs'])
    finally:
        shutil.rmtree(temp_path, ignore_errors=True)
    logging.info(f'[media]\tdone, {len(posts)} release(s) restored')
