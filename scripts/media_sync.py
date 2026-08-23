#!/usr/bin/env python3
"""Keep each post's media.zip and its GitHub release asset in sync with disk.

Disk is the source of truth. For every post this compares three things:

    posts/<post>/{img,vid,ext}/**   <->   posts/<post>/media.zip   <->   release asset

and repairs whatever drifted: rebuilds the zip when files were added, removed
or edited, then re-uploads it when it no longer matches the release.

Nothing here touches git. Committing and pushing stay entirely yours, so this
is safe to run on a draft you have not decided to publish yet.

Usage:
    uv run scripts/media_sync.py                  # scan all, ask before uploading
    uv run scripts/media_sync.py 251020           # one post (id, dir name or path)
    uv run scripts/media_sync.py --check          # report only, exit 1 on drift
    uv run scripts/media_sync.py --changed        # only posts touched per git status
    uv run scripts/media_sync.py --local          # rebuild zips, never upload
    uv run scripts/media_sync.py --yes            # upload without asking
    uv run scripts/media_sync.py --create-missing # also create absent releases

Requires the `gh` CLI, logged in with `repo` scope. Standard library only.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
import zlib

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSTS_DIR = os.path.join(REPO_ROOT, 'posts')
MEDIA_FILE = 'media.zip'

# Directories whose contents ship in media.zip.
MEDIA_DIRS = ('img', 'vid', 'ext')
# Working directories that stay on your disk and never get uploaded.
SCRATCH_DIRS = ('raw', 'unslimmed', 'orig', 'src')
# Text files that live in git rather than in the zip.
TEXT_FILES = ('index.md', 'meta.md')

# Formats deflate cannot shrink: webp and video come out a few bytes *larger*,
# so store them as-is. Older posts hold jpg/png screenshots that do still gain
# ~10%, so those stay deflated.
STORED_EXT = ('.webp', '.mp4', '.webm', '.mov', '.m4v', '.heic', '.heif',
              '.zip', '.gz', '.mp3', '.m4a', '.avif')

CHUNK = 1024 * 1024


# --------------------------------------------------------------------------
# shell helpers
# --------------------------------------------------------------------------

def run(cmd, check=True, capture=True):
    """Run a command, returning stdout. Raises RuntimeError on failure."""
    proc = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    if check and proc.returncode != 0:
        err = (proc.stderr or proc.stdout or '').strip()
        raise RuntimeError(f'{" ".join(cmd)} failed ({proc.returncode}): {err}')
    return (proc.stdout or '').strip()


def gh_available():
    try:
        run(['gh', 'auth', 'status'])
        return True
    except (RuntimeError, FileNotFoundError):
        return False


def get_repo():
    """owner/name for the current repository."""
    try:
        return run(['gh', 'repo', 'view', '--json', 'nameWithOwner',
                    '-q', '.nameWithOwner'])
    except (RuntimeError, FileNotFoundError):
        url = run(['git', '-C', REPO_ROOT, 'remote', 'get-url', 'origin'])
        return re.sub(r'^.*github\.com[:/]|\.git$', '', url)


# --------------------------------------------------------------------------
# repository state
# --------------------------------------------------------------------------

def list_tags():
    """Every tag on the remote, so a post can be matched to its release."""
    try:
        out = run(['git', '-C', REPO_ROOT, 'ls-remote', '--tags', 'origin'])
    except (RuntimeError, FileNotFoundError):
        return set()  # offline or no remote; only --local makes sense then
    tags = set()
    for line in out.splitlines():
        parts = line.split('refs/tags/')
        if len(parts) == 2:
            tags.add(parts[1].removesuffix('^{}'))
    return tags


def list_releases(repo):
    """tag -> media.zip asset info, in a single paginated call."""
    raw = run(['gh', 'api', '--paginate', '--slurp',
               f'repos/{repo}/releases?per_page=100'])
    releases = {}
    for page in json.loads(raw):
        for rel in page:
            asset = next((a for a in rel.get('assets', [])
                          if a['name'] == MEDIA_FILE), None)
            releases[rel['tag_name']] = {
                'name': rel.get('name') or '',
                'size': asset['size'] if asset else None,
                # GitHub reports "sha256:<hex>"; None on very old assets.
                'digest': (asset.get('digest') or '').removeprefix('sha256:')
                if asset else None,
            }
    return releases


def tag_for_post(post, tags):
    """Longest tag that prefixes the post directory name.

    Posts are named <date>-<slug> and tagged <date>, so 251020-wazzup-beijing
    resolves to 251020. When two posts share a date (221003-glibc-openwrt and
    221003-glibc-openwrt-en) the date tag alone is ambiguous; giving the second
    post its own more specific tag makes it win here automatically.
    """
    best = None
    for tag in tags:
        if post == tag or post.startswith(tag + '-'):
            if best is None or len(tag) > len(best):
                best = tag
    return best


def changed_posts():
    """Posts with uncommitted or unpushed changes, per git."""
    names = set()
    out = run(['git', '-C', REPO_ROOT, 'status', '--porcelain', '--', 'posts'])
    for line in out.splitlines():
        names.add(line[3:].strip().strip('"'))
    try:
        upstream = run(['git', '-C', REPO_ROOT, 'rev-parse',
                        '--abbrev-ref', '@{upstream}'])
        out = run(['git', '-C', REPO_ROOT, 'diff', '--name-only',
                   upstream, 'HEAD', '--', 'posts'])
        names.update(out.splitlines())
    except RuntimeError:
        pass  # no upstream configured; working-tree changes are enough

    posts = set()
    for path in names:
        parts = path.replace('\\', '/').split('/')
        if len(parts) >= 2 and parts[0] == 'posts':
            posts.add(parts[1])
    return posts


# --------------------------------------------------------------------------
# media inspection
# --------------------------------------------------------------------------

def scan_media(post_path):
    """Relative posix paths of every media file on disk, sorted."""
    found = []
    for media_dir in MEDIA_DIRS:
        root = os.path.join(post_path, media_dir)
        if not os.path.isdir(root):
            continue
        for dirpath, _, filenames in os.walk(root):
            for filename in filenames:
                full = os.path.join(dirpath, filename)
                rel = os.path.relpath(full, post_path).replace(os.sep, '/')
                found.append(rel)
    return sorted(found)


def unexpected_dirs(post_path):
    """Directories that are neither media nor known scratch space."""
    out = []
    for entry in sorted(os.listdir(post_path)):
        if not os.path.isdir(os.path.join(post_path, entry)):
            continue
        if entry not in MEDIA_DIRS and entry not in SCRATCH_DIRS:
            out.append(entry)
    return out


def crc32_of(path):
    crc = 0
    with open(path, 'rb') as f:
        while chunk := f.read(CHUNK):
            crc = zlib.crc32(chunk, crc)
    return crc & 0xFFFFFFFF


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def zip_manifest(zip_path):
    """name -> (size, crc) for the files inside a zip.

    Some older zips were written on Windows with backslash separators, which
    unzip(1) silently normalises but Python's zipfile does not; treat both
    spellings as the same path.
    """
    manifest = {}
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace('\\', '/')
            manifest[name] = (info.file_size, info.CRC)
    return manifest


def disk_manifest(post_path, files, fast=False):
    manifest = {}
    for rel in files:
        full = os.path.join(post_path, rel.replace('/', os.sep))
        size = os.path.getsize(full)
        manifest[rel] = (size, None if fast else crc32_of(full))
    return manifest


def compare(disk, zipped, fast=False):
    """(added, removed, modified) between disk and zip manifests."""
    added = sorted(set(disk) - set(zipped))
    removed = sorted(set(zipped) - set(disk))
    modified = []
    for name in sorted(set(disk) & set(zipped)):
        d_size, d_crc = disk[name]
        z_size, z_crc = zipped[name]
        if d_size != z_size:
            modified.append(name)
        elif not fast and d_crc != z_crc:
            modified.append(name)
    return added, removed, modified


def build_zip(post_path, files):
    """Write media.zip from the files on disk, atomically."""
    zip_path = os.path.join(post_path, MEDIA_FILE)
    tmp_path = zip_path + '.tmp'
    with zipfile.ZipFile(tmp_path, 'w') as zf:
        for rel in files:
            full = os.path.join(post_path, rel.replace('/', os.sep))
            ext = os.path.splitext(rel)[1].lower()
            compress = (zipfile.ZIP_STORED if ext in STORED_EXT
                        else zipfile.ZIP_DEFLATED)
            zf.write(full, arcname=rel, compress_type=compress)
    os.replace(tmp_path, zip_path)
    return zip_path


# --------------------------------------------------------------------------
# markdown reference check
# --------------------------------------------------------------------------

FENCE = re.compile(r'^\s*(```|~~~)')
# ![alt](img/a.webp "optional title")  and  [text](vid/b.webm)
LINK = re.compile(r'!?\[[^\]]*\]\(\s*([^)\s]+)')
# {{< video src="vid/b.webm" >}}, <img src="img/a.webp">
SRC = re.compile(r'src\s*=\s*["\']([^"\']+)')
# meta.md front matter: image: "img/cover.webp"
FIELD = re.compile(r'^\s*image:\s*["\']?([^"\'\s]+)')


# /p/<slug>/img/x.webp, ../p/<slug>/img/x.webp, ../<dir>/img/x.webp
CROSS_POST = re.compile(r'^(?:\.\./)*/?(?:p/)?(\d{6}-[^/]+)/(.+)$')
SKIP_SCHEMES = ('http://', 'https://', '//', 'data:', 'mailto:', '#')


def resolve_ref(raw, post_name):
    """Map one markdown reference to (post directory, relative media path).

    Posts reference their own media as img/x.webp, but also each other's as
    /p/<slug>/img/x.webp or ../p/<slug>/img/x.webp, since the published slug
    equals the post directory name. Returns None for anything that is not a
    media file inside this repository.
    """
    ref = raw.split('#')[0].split('?')[0].strip()
    if not ref or ref.startswith(SKIP_SCHEMES):
        return None
    ref = ref.removeprefix('./')

    match = CROSS_POST.match(ref)
    if match:
        target, tail = match.group(1), match.group(2)
    else:
        target, tail = post_name, ref

    if not tail.startswith(tuple(d + '/' for d in MEDIA_DIRS)):
        return None
    return target, tail


def build_reference_index(post_names):
    """post directory -> set of media paths referenced from anywhere.

    Repository-wide, so an image used only by a *different* post is not
    mistaken for an orphan.
    """
    index = {name: set() for name in post_names}
    for name in post_names:
        post_path = os.path.join(POSTS_DIR, name)
        for filename in TEXT_FILES:
            path = os.path.join(post_path, filename)
            if not os.path.isfile(path):
                continue
            in_fence = False
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    if FENCE.match(line):
                        in_fence = not in_fence
                        continue
                    if in_fence:
                        continue
                    candidates = (LINK.findall(line) + SRC.findall(line)
                                  + FIELD.findall(line))
                    for cand in candidates:
                        resolved = resolve_ref(cand, name)
                        if resolved is None:
                            continue
                        target, tail = resolved
                        index.setdefault(target, set()).add(tail)
    return index


# --------------------------------------------------------------------------
# per-post work
# --------------------------------------------------------------------------

class Post:
    def __init__(self, name, path):
        self.name = name
        self.path = path
        self.tag = None
        self.files = []
        self.missing_refs = []
        self.orphans = []
        self.strays = []
        self.added = []
        self.removed = []
        self.modified = []
        self.zip_rebuilt = False
        self.needs_upload = False
        self.release_missing = False
        self.ambiguous = False
        self.notes = []

    @property
    def zip_path(self):
        return os.path.join(self.path, MEDIA_FILE)

    @property
    def zip_stale(self):
        return bool(self.added or self.removed or self.modified)

    @property
    def blocked(self):
        return bool(self.missing_refs)

    def status(self):
        if self.missing_refs:
            return 'BROKEN REFS'
        if not self.files:
            return 'no media'
        if self.ambiguous:
            return 'SHARED TAG'
        if self.release_missing:
            return 'NO RELEASE'
        if self.zip_stale and self.needs_upload:
            return 'zip + release stale'
        if self.zip_stale:
            return 'zip stale'
        if self.needs_upload:
            return 'release stale'
        return 'ok'


def inspect(name, tags, releases, refs_index, fast=False):
    post = Post(name, os.path.join(POSTS_DIR, name))
    post.tag = tag_for_post(name, tags)
    post.files = scan_media(post.path)
    post.strays = unexpected_dirs(post.path)

    refs = refs_index.get(name, set())
    on_disk = set(post.files)
    post.missing_refs = sorted(refs - on_disk)
    post.orphans = sorted(on_disk - refs)

    if not post.files:
        return post

    if os.path.isfile(post.zip_path):
        zipped = zip_manifest(post.zip_path)
    else:
        zipped = {}
        post.notes.append('media.zip does not exist yet')
    disk = disk_manifest(post.path, post.files, fast=fast)
    post.added, post.removed, post.modified = compare(disk, zipped, fast=fast)

    if post.tag is None:
        post.release_missing = True
        post.notes.append('no tag matches this post directory')
        return post

    release = releases.get(post.tag)
    if release is None:
        post.release_missing = True
        post.notes.append(f'no release tagged {post.tag}')
        return post

    matches_release = False
    if release['digest']:
        matches_release = sha256_of(post.zip_path) == release['digest']
    elif release['size'] is not None:
        matches_release = os.path.getsize(post.zip_path) == release['size']

    # One release holds one media.zip, so when two post directories resolve to
    # the same tag only one of them can own it. Whichever already matches the
    # published asset is the owner; uploading from the other would silently
    # replace the owner's media, so refuse and say why.
    siblings = [p for p in tag_owners(post.tag, tags) if p != name]
    if siblings and not (matches_release and not post.zip_stale):
        post.ambiguous = True
        post.notes.append(
            'tag {} is owned by {}; this post\'s local media is not published '
            'from here. Give it its own tag if it needs separate media.'
            .format(post.tag, ', '.join(siblings)))
        return post

    if post.zip_stale:
        post.needs_upload = True  # the rebuilt zip will differ by definition
    elif release['digest'] is None and release['size'] is None:
        post.needs_upload = True
        post.notes.append('release has no media.zip asset')
    else:
        post.needs_upload = not matches_release
    return post


_owner_cache = {}


def tag_owners(tag, tags):
    """Every post directory that resolves to this tag."""
    if tag not in _owner_cache:
        _owner_cache[tag] = [
            name for name in sorted(os.listdir(POSTS_DIR))
            if os.path.isdir(os.path.join(POSTS_DIR, name))
            and tag_for_post(name, tags) == tag
        ]
    return _owner_cache[tag]


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

def human(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if abs(n) < 1024 or unit == 'GB':
            return f'{n:.0f}{unit}' if unit == 'B' else f'{n:.1f}{unit}'
        n /= 1024


def report(post, verbose=False):
    status = post.status()
    if status == 'ok' and not verbose:
        return
    print(f'  {post.name:<32} {status}')
    for ref in post.missing_refs:
        print(f'      ! referenced but not on disk: {ref}')
    for name in post.added:
        print(f'      + {name}')
    for name in post.removed:
        print(f'      - {name}')
    for name in post.modified:
        print(f'      ~ {name}')
    for note in post.notes:
        print(f'      . {note}')
    for stray in post.strays:
        print(f'      . directory {stray}/ is neither media nor scratch, '
              f'skipped')
    if verbose:
        for orphan in post.orphans:
            print(f'      ? on disk but never referenced: {orphan}')


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def resolve_targets(args_posts):
    """Map user arguments (id, dir name or path) to post directory names."""
    everything = sorted(n for n in os.listdir(POSTS_DIR)
                        if os.path.isdir(os.path.join(POSTS_DIR, n)))
    if not args_posts:
        return everything
    chosen = []
    for arg in args_posts:
        key = os.path.basename(os.path.normpath(arg.replace('\\', '/')))
        matches = [n for n in everything if n == key or n.startswith(key + '-')]
        if not matches:
            sys.exit(f'no post matches {arg!r}')
        chosen.extend(matches)
    return sorted(set(chosen))


def main():
    parser = argparse.ArgumentParser(
        description='Sync post media with media.zip and its GitHub release.')
    parser.add_argument('posts', nargs='*',
                        help='post id, directory name or path (default: all)')
    parser.add_argument('--check', action='store_true',
                        help='report only, change nothing, exit 1 on drift')
    parser.add_argument('--changed', action='store_true',
                        help='limit to posts git reports as modified')
    parser.add_argument('--local', action='store_true',
                        help='rebuild media.zip but never upload')
    parser.add_argument('--fast', action='store_true',
                        help='compare by size only, skipping CRC checks')
    parser.add_argument('--yes', '-y', action='store_true',
                        help='do not ask before uploading')
    parser.add_argument('--create-missing', action='store_true',
                        help='create a release for posts that have none')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='also list posts already in sync and orphan files')
    args = parser.parse_args()

    if not args.local and not gh_available():
        sys.exit('gh CLI not available or not logged in; '
                 'use --local to rebuild zips only')

    targets = resolve_targets(args.posts)
    if args.changed:
        touched = changed_posts()
        targets = [t for t in targets if t in touched]
        if not targets:
            print('no post has uncommitted or unpushed changes')
            return 0

    tags = list_tags()
    releases = {} if args.local else list_releases(get_repo())
    # Built from every post, not just the targets, so a cross-post reference
    # still counts as a use.
    all_posts = sorted(n for n in os.listdir(POSTS_DIR)
                       if os.path.isdir(os.path.join(POSTS_DIR, n)))
    refs_index = build_reference_index(all_posts)

    print(f'scanning {len(targets)} post(s)...')
    posts = [inspect(t, tags, releases, refs_index, fast=args.fast)
             for t in targets]
    for post in posts:
        report(post, verbose=args.verbose)

    def actionable(post):
        return not post.blocked and not post.ambiguous

    broken = [p for p in posts if p.blocked]
    shared = [p for p in posts if p.ambiguous]
    stale_zip = [p for p in posts if p.zip_stale and actionable(p)]
    stale_rel = [p for p in posts if p.needs_upload and actionable(p)]
    no_release = [p for p in posts if p.release_missing and p.files
                  and actionable(p)]

    if broken:
        print(f'\n{len(broken)} post(s) reference media that is not on disk. '
              f'Fix those first: rebuilding their zip would break the live '
              f'page.')
    if shared:
        print(f'\n{len(shared)} post(s) share a tag with another post and were '
              f'left alone, so their media cannot overwrite the tag owner\'s.')

    if not stale_zip and not stale_rel and not no_release:
        if broken:
            return 2
        print('\neverything in sync')
        return 0

    if args.check:
        print(f'\ndrift: {len(stale_zip)} zip(s), {len(stale_rel)} release(s), '
              f'{len(no_release)} missing release(s)')
        return 2 if broken else 1

    if broken:
        print('nothing was rebuilt for the post(s) above; '
              'the rest is handled below.')

    # 1. rebuild stale zips
    for post in stale_zip:
        build_zip(post.path, post.files)
        post.zip_rebuilt = True
        size = os.path.getsize(post.zip_path)
        print(f'rebuilt {post.name}/media.zip '
              f'({len(post.files)} files, {human(size)})')

    if args.local:
        if stale_rel:
            print(f'\n--local: skipped uploading {len(stale_rel)} release(s)')
        return 0

    # 2. upload what no longer matches the release
    uploads = [p for p in stale_rel if not p.release_missing]
    creates = no_release if args.create_missing else []

    if no_release and not args.create_missing:
        for post in no_release:
            print(f'\n{post.name} has no release to upload to. '
                  f'Re-run with --create-missing to create one.')

    if not uploads and not creates:
        return 0

    print()
    for post in uploads:
        print(f'upload  {post.tag}  <- {post.name}/media.zip '
              f'({human(os.path.getsize(post.zip_path))})')
    for post in creates:
        print(f'create  {post.tag or post.name}  '
              f'<- {post.name}/media.zip (new release)')

    if not args.yes:
        answer = input('\nproceed? [y/N] ').strip().lower()
        if answer not in ('y', 'yes'):
            print('aborted; zips on disk are up to date')
            return 0

    # capture=False so gh's own upload progress stays visible; these files
    # run to tens of megabytes.
    for post in uploads:
        run(['gh', 'release', 'upload', post.tag, post.zip_path, '--clobber'],
            capture=False)
        print(f'uploaded {post.tag}')
    for post in creates:
        tag = post.tag or post.name.split('-')[0]
        run(['gh', 'release', 'create', tag,
             post.zip_path,
             '--title', tag,
             '--notes', f'media for {post.name}'], capture=False)
        print(f'created {tag}')

    print('\ndone. The site still needs a build to pick this up.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
