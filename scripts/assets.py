"""Fetch the site's chrome images into assets/ before Hugo runs.

params.favicon and params.sidebar.avatar are resolved through assets/, so a
URL in either one makes Hugo re-fetch the file on every build via
resources.GetRemote -- behind a short timeout, with nothing but a warning and
a broken image when the host is slow. Pulling them down here instead gives
Hugo a local resource to fingerprint, and still keeps the binaries out of git
(every image extension is gitignored).

Run from the repository root, after build.sh has flattened hugo/ into it.
"""

import os
import logging
import requests


logging.basicConfig(level=logging.INFO)

pwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
assets_path = os.path.join(pwd, 'assets')

# destination under assets/  ->  where to get it
assets = {
    'img/avatar.png': 'https://kmtea.eu/res/2206/avatar.png',
}


def fetch(rel_path, url):
    target = os.path.join(assets_path, rel_path)
    os.makedirs(os.path.dirname(target), exist_ok=True)

    r = requests.get(url, timeout=60)
    # A 404 page written to avatar.png would sail through Hugo as a
    # zero-height image rather than failing the build.
    r.raise_for_status()
    if not r.content:
        raise RuntimeError(f'[assets]\t{url} returned an empty body')

    with open(target, 'wb') as f:
        f.write(r.content)
    logging.info(f'[assets]\t{rel_path} <- {url} ({len(r.content)} bytes)')


if __name__ == '__main__':
    for rel_path, url in assets.items():
        fetch(rel_path, url)
    logging.info(f'[assets]\tdone, {len(assets)} file(s) fetched')
