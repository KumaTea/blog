# Publishing workflow

The repository stays light because no media is committed to `main`. Images and
videos live in a `media.zip` attached to a GitHub release, and CI pulls them
back in at build time. That is the only reason publishing has more than one
step.

Nothing below commits, pushes or tags on your behalf. Drafts stay drafts until
you decide to `git add .`.

## Prerequisites, once

- `gh auth login` — used for releases; it never touches your GPG key.
- `uv` on PATH. No script needs a virtualenv; `uv run` handles dependencies.
- Optional, to stop retyping your signing passphrase all day, in
  `~/.gnupg/gpg-agent.conf`:

  ```
  default-cache-ttl 28800
  max-cache-ttl 28800
  ```

## A new post

1. **Write.** Create `posts/<yymmdd>-<slug>/` with `index.md` (body, starting
   at `# Title`) and `meta.md` (front matter). The directory name becomes the
   URL: `posts/260531-hk-trip` publishes at `/p/260531-hk-trip/`.

2. **Convert the photos.** Drop originals in `raw/` inside the post directory,
   then:

   ```bash
   uv run --with-requirements scripts/requirements-local.txt scripts/img/cvt2webp.py --dir posts/<post>/raw
   ```

   Move the resulting `.webp` files into `posts/<post>/img/`. Videos go through
   `scripts/img/video.sh <input>` into `posts/<post>/vid/`.

3. **Check what will ship.**

   ```bash
   uv run scripts/media_sync.py <post> --check
   ```

   This lists images referenced but missing, images on disk that nothing
   references, and how the zip differs from what is published.

4. **Commit and push the text.** `raw/` and every image extension are
   gitignored, so `git add .` picks up only `index.md` and `meta.md`.

   ```bash
   git add . && git commit -S -m "new post: <title>" && git push
   ```

5. **Publish the media.** The release has to exist before CI builds, or the
   post goes live with broken images.

   ```bash
   uv run scripts/media_sync.py <post> --create-missing
   ```

6. **Trigger the build.** CI only runs when the commit message contains
   `build`:

   ```bash
   git commit --allow-empty -S -m "[build] <title>" && git push
   ```

   Or skip the empty commit and dispatch the workflow directly:

   ```bash
   gh workflow run gh-pages.yml --ref main
   ```

## Updating a post

Editing text only — no media touched:

```bash
git add . && git commit -S -m "[build] update post: <title>" && git push
```

Media changed, whether you added an image, replaced one or deleted one:

```bash
uv run scripts/media_sync.py --changed
git add . && git commit -S -m "[build] update post: <title>" && git push
```

`media_sync.py` rebuilds `media.zip` and replaces the release asset in place,
so there is no deleting and re-uploading through the web UI.

## Keeping a draft unpublished

Write and convert as much as you like: none of it reaches GitHub until you
commit. `media_sync.py` will not create a release for a post that has none
unless you pass `--create-missing`, so a plain run can never publish a draft's
photos by accident. To rebuild a draft's zip locally without any upload at all:

```bash
uv run scripts/media_sync.py <post> --local
```

## Scripts

| Script | Runs | Does |
| --- | --- | --- |
| `media_sync.py` | your machine | Compares disk against `media.zip` against the release, rebuilds and re-uploads whatever drifted. Never touches git. |
| `img/cvt2webp.py` | your machine | Converts jpg/png/HEIC to webp, named from EXIF capture time, orientation applied. |
| `img/video.sh` | your machine | Re-encodes a video to 1080p HEVC. |
| `dlext.py` | your machine | Downloads externally hosted images into `ext/` and rewrites the links. |
| `build.sh` | CI | Lays out the Hugo tree, then runs the four below. |
| `assets.py` | CI | Fetches the favicon and sidebar avatar into `assets/`. |
| `meta.py` | CI | Merges `meta.md` into `index.md`, adds `lastmod` from git history, fills the about page dates. |
| `media.py` | CI | Unpacks every release's `media.zip` into its post, downloading only the ones that changed. |
| `slug.py` | CI | Adds `slug:` to the front matter so URLs stay `/p/<directory name>/`. |

`meta.py` rewrites `index.md` in place and deletes `meta.md`, so it is only
ever safe to run on a disposable CI checkout, never in your working tree.

## How a post finds its media

A post directory is matched to the release whose tag is the longest prefix of
its name, so `posts/251020-wazzup-beijing` takes its media from tag `251020`.

When two posts share a date, they also share that tag, and one release can only
hold one `media.zip`. `media_sync.py` detects this, leaves both posts alone and
says so rather than letting one overwrite the other. If the second post really
does need its own media, give it a longer tag — a release tagged
`221003-glibc-openwrt-en` wins over `221003` for that directory automatically.

## Build time

Hugo resizes and re-encodes every image on every build, which was almost all of
a four-minute build. The workflow now caches `resources/_gen`, where that output
is stored under content hashes, so only new or changed images cost anything.

The release archives are cached the same way. `media.py` asks the API what each
release currently publishes (a sha256 digest where GitHub has one, the asset id
and mtime otherwise) and keeps the zips in `/tmp/blog_media` between runs, so a
build only pulls down the posts whose media actually moved instead of all
~180 MB. The cache is stored under a hash of those fingerprints, which means an
unchanged set of releases reuses the entry it already has rather than uploading
a fresh copy every build.

If the API is unreachable or rate limited, `media.py` says so and downloads
everything, which is what it always did. Set `GITHUB_TOKEN` to keep that from
happening (the workflow already does).
