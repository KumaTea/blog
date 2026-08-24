import os
import re
import json
import random
import string
import shutil
import hashlib
import logging
import zipfile
import tempfile
import requests
import urllib.request
from data import posts, pwd, repo, api_headers


logging.basicConfig(level=logging.INFO)

media_file = 'media.zip'
download_url = 'https://github.com/{repo}/releases/download/{tag}/{name}'
releases_api = f'https://api.github.com/repos/{repo}/releases'

# Every build re-downloaded the full ~180 MB of release archives, almost all
# of it bytes it already had. The zips now live in a directory the workflow
# caches between runs, and a release is only fetched again when the asset
# published under its tag no longer matches the one on disk.
cache_dir = os.environ.get('MEDIA_CACHE_DIR') or os.path.join(
    tempfile.gettempdir(), 'blog-media-cache')
manifest_file = os.path.join(cache_dir, 'manifest.json')

chunk_size = 1024 * 1024


def get_fingerprints():
    """tag -> a string identifying the media.zip currently published there.

    GitHub returns a sha256 digest for anything uploaded in the last couple of
    years; older assets have none, so those fall back to the asset id, which
    `gh release upload --clobber` replaces on every re-upload, together with
    size and mtime.

    An unreachable or rate-limited API returns {}, which makes every release
    look unrecognised and restores the old download-everything behaviour
    rather than serving whatever stale zip happens to be cached.
    """
    fingerprints = {}
    page = 1
    while True:
        try:
            r = requests.get(releases_api, headers=api_headers(),
                             params={'per_page': 100, 'page': page},
                             timeout=30)
            r.raise_for_status()
            batch = r.json()
        except (requests.RequestException, ValueError) as e:
            logging.warning(f'[media]\tcould not list releases ({e}), '
                            f'downloading every archive')
            return {}
        if not batch:
            break
        for release in batch:
            asset = next((a for a in release.get('assets', [])
                          if a['name'] == media_file), None)
            if asset is None:
                continue  # a release can exist without media, e.g. a tarball
            fingerprints[release['tag_name']] = asset.get('digest') or (
                f"{asset['id']}:{asset['size']}:{asset['updated_at']}")
        if len(batch) < 100:
            break
        page += 1
    return fingerprints


def load_manifest():
    """What the cached zips were when they were written."""
    try:
        with open(manifest_file, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}  # first build, or a cache entry that got truncated


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def download(post_id, target):
    url = download_url.format(repo=repo, tag=post_id, name=media_file)
    partial = target + '.part'

    r = requests.get(url, stream=True, timeout=300)
    # Without this a 404 quietly writes GitHub's error page into media.zip and
    # the build carries on with missing images.
    r.raise_for_status()
    with open(partial, 'wb') as f:
        for chunk in r.iter_content(chunk_size=chunk_size):
            f.write(chunk)
    # Rename last so an interrupted build cannot leave a half-written archive
    # behind for the next one to trust.
    os.replace(partial, target)
    return os.path.getsize(target)


def get_media(post_id, want, cached):
    """(path, reused) for this release's media.zip, fetched only if it moved."""
    target = os.path.join(cache_dir, f'{post_id}.zip')

    if want and cached.get(post_id) == want and os.path.exists(target):
        logging.info(f'[media]\tpost {post_id} cached '
                     f'({os.path.getsize(target)} bytes)')
        return target, True

    size = download(post_id, target)
    if want and want.startswith('sha256:'):
        if sha256_of(target) != want.removeprefix('sha256:'):
            raise RuntimeError(f'[media]\t{post_id} archive does not match '
                               f'the digest GitHub publishes for it')
    # An empty fingerprint never equals a real one, so a release we could not
    # identify is downloaded again next build instead of going stale.
    cached[post_id] = want or ''
    logging.info(f'[media]\tpost {post_id} downloaded ({size} bytes)')
    return target, False


def prune(cached, keep):
    """Forget releases this build no longer pulls from."""
    for name in os.listdir(cache_dir):
        if name.endswith('.zip') and name[:-4] not in keep:
            os.remove(os.path.join(cache_dir, name))
            logging.info(f'[media]\tevicted {name}')
    for post_id in set(cached) - set(keep):
        del cached[post_id]


def save_manifest(cached):
    with open(manifest_file, 'w', encoding='utf-8') as f:
        json.dump(cached, f, sort_keys=True, indent=1)


def publish_cache_key(cached):
    """Hand the workflow a key to store this cache under.

    Keying on the contents means an unchanged set of releases hashes to the
    key the cache is already saved under, so the workflow can skip re-uploading
    180 MB it already has. A cache holding anything we could not fingerprint is
    still usable, but it must not be published as if it were complete.
    """
    github_output = os.environ.get('GITHUB_OUTPUT')
    if not github_output or not all(cached.values()):
        return
    blob = json.dumps(cached, sort_keys=True, separators=(',', ':'))
    key = hashlib.sha256(blob.encode()).hexdigest()[:16]
    with open(github_output, 'a', encoding='utf-8') as f:
        f.write(f'media_key={key}\n')
    logging.info(f'[media]\tcache key {key}')


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

    os.makedirs(cache_dir, exist_ok=True)
    logging.info(f'[media]\tusing cache directory {cache_dir}')
    cached = load_manifest()
    fingerprints = get_fingerprints()

    prune(cached, posts)
    reused = 0
    for pid, info in sorted(posts.items()):
        archive, from_cache = get_media(pid, fingerprints.get(pid), cached)
        reused += from_cache
        unzip_media(pid, archive, info['dirs'])

    save_manifest(cached)
    publish_cache_key(cached)
    logging.info(f'[media]\tdone, {len(posts)} release(s) restored, '
                 f'{reused} from cache')
