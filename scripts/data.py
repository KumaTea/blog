import os
import requests


# Each post directory is named <yymmdd>-<slug>, and the media for it is
# published as a release whose tag prefixes that name:
#
#     posts/251020-wazzup-beijing  <-  tag 251020
#
# posts maps every tag that has media to the post directories it feeds:
#
#     posts = {
#         '220616': {'name': 'we-in-game', 'dirs': ['220616-we-in-game']},
#     }
#
# 'name' is kept for callers that only want the slug of the primary post.

repo = 'KumaTea/blog'
pwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
posts_path = os.path.join(pwd, 'posts')
tags_api = f'https://api.github.com/repos/{repo}/git/refs/tags'


def get_tags():
    """Every tag in the repository.

    The API pages at 30 by default, so an unpaginated read silently starts
    dropping the oldest posts' media once the repository passes 30 tags.
    """
    tags = []
    page = 1
    while True:
        r = requests.get(tags_api, params={'per_page': 100, 'page': page},
                         timeout=30)
        if r.status_code == 404:
            break  # repository has no tags at all
        r.raise_for_status()
        batch = r.json()
        if not isinstance(batch, list):  # single tag comes back as an object
            batch = [batch]
        if not batch:
            break
        tags += [i['ref'].split('/')[-1] for i in batch]
        if len(batch) < 100:
            break
        page += 1
    return tags


def tag_of(post_dir, tags):
    """The longest tag that prefixes this post directory name.

    Longest wins so that two posts sharing a date can be told apart: both
    221003-glibc-openwrt and 221003-glibc-openwrt-en match tag 221003, but
    giving the second one a tag of its own makes it match that instead.
    """
    best = None
    for tag in tags:
        if post_dir == tag or post_dir.startswith(tag + '-'):
            if best is None or len(tag) > len(best):
                best = tag
    return best


def get_posts():
    tags = get_tags()
    post_dirs = sorted(
        d for d in os.listdir(posts_path)
        if os.path.isdir(os.path.join(posts_path, d))
    )

    found = {}
    for post_dir in post_dirs:
        tag = tag_of(post_dir, tags)
        if tag is None:
            continue  # no release, so no media to restore
        entry = found.setdefault(tag, {'name': None, 'dirs': []})
        entry['dirs'].append(post_dir)
        slug = post_dir[len(tag):].lstrip('-')
        if entry['name'] is None or len(slug) < len(entry['name']):
            entry['name'] = slug
    return found


posts = get_posts()
