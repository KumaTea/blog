# Instructions for AI agents

**TL;DR** — You do the media: convert → move into `img/` / `vid/` → rewrite
the post's media paths → check → zip → upload *only* with a yes. You never
commit, never push, never write prose. [`WORKFLOW.md`](WORKFLOW.md) is the
full pipeline and the reasoning behind it; this file is only what changes when
an agent, not the user, is at the keyboard. Read both.

## Yours and theirs

The user writes `index.md` and `meta.md`, drops the originals, decides the
category, cover and prose, and commits with their GPG key. You own everything
between the originals and the published release asset — including the media
paths inside the post, because the user links the originals they dropped and
only you know what those files became.

## Never

- Commit, push, tag, amend or stash. Every commit here is signed by the user;
  `WORKFLOW.md` tells *them* to commit, not you.
- Run `scripts/meta.py` or `scripts/slug.py` in the working tree. `meta.py`
  merges `meta.md` into `index.md` and then deletes `meta.md`. Both are
  CI-only steps (`scripts/build.sh`) and destroy source files here.
- Change anything in `index.md` or `meta.md` except media paths. Prose,
  captions, titles, front-matter values and ordering are the user's.
- Upload without a go-ahead in the same turn.
- Delete anything in `raw/`, or `git add -f` media.

## The pipeline

```bash
# 1. photos (jpg/png/HEIC) -> webp, every source name kept as-is
#    --python 3.12 is not optional here; see below
uv run --python 3.12 --with-requirements scripts/requirements-local.txt \
    scripts/img/cvt2webp.py --dir posts/<post>/raw --keep-names
mkdir -p posts/<post>/img && mv posts/<post>/raw/*.webp posts/<post>/img/

# 2. videos -> vid/, output path spelled out, so the name is yours to keep
bash scripts/img/video.sh posts/<post>/raw/clip.mov posts/<post>/vid/clip.mp4

# 3. point the post at what you just produced (see the next section)

# 4. broken refs, orphans, drift (exit 0 in sync, 1 drift, 2 broken refs)
uv run scripts/media_sync.py <post> --check -v

# 5. rebuild media.zip — no network, safe on a draft
uv run scripts/media_sync.py <post> --local

# 6. publish, only after the user says yes
uv run scripts/media_sync.py <post> --yes                  # existing release
uv run scripts/media_sync.py <post> --yes --create-missing # first publication
```

Then report what you converted, what you rewrote and what you uploaded. The
user commits and pushes a message containing `build`.

## Names, and rewriting the references

The user usually renames the originals before writing, and links them where
they lie:

```markdown
![some caption](raw/renamed.heif)
```

The name in `raw/` is therefore already the name the post depends on. **Keep
it.**

- Convert with `--keep-names`. Without it `cvt2webp.py` renames every output
  from EXIF capture time, which breaks each of those links silently — and two
  photos shot in the same second collapse into `IMG_….webp` and
  `IMG_…_1.webp`, so even reconstructing the mapping is guesswork.
- Then rewrite each reference to where the file now is, changing the directory
  and the extension and nothing else:

  | in the draft | after you convert |
  | --- | --- |
  | `![some caption](raw/renamed.heif)` | `![some caption](img/renamed.webp)` |
  | `image: "raw/cover.heif"` | `image: "img/cover.webp"` |
  | `{{< video src="raw/clip.mov" >}}` | `{{< video src="vid/clip.mp4" >}}` |

- Edit only the path, inside the parentheses or the quotes. Captions, alt
  text, link titles and the rest of the line stay byte for byte.
- Both files can carry references: `index.md` anywhere, `meta.md` in its
  `image:` field.
- Drop `--keep-names` only for an unsorted camera dump, where EXIF naming is
  the point. The user has written no references to such files yet, so report
  the mapping and let them place the photos.

Confirm nothing was missed:

```bash
grep -n 'raw/' posts/<post>/index.md posts/<post>/meta.md   # expect no output
```

`media_sync.py` counts only `img/`, `vid/` and `ext/` paths as references, so
a link you forgot to rewrite does **not** surface as `BROKEN REFS`. It
surfaces as its converted file looking like an orphan under `--check -v`
(`? on disk but never referenced`). Treat an unexpected orphan as a missed
rewrite before treating it as a spare file.

## What WORKFLOW.md does not tell you

- `media_sync.py` asks for confirmation on **stdin**, which hangs in a
  non-interactive tool call. `--check` and `--local` are always safe; for an
  upload, ask in chat, then pass `--yes`.
- An upload publishes to a public release, and `--create-missing` publishes a
  draft's photos. Neither is reversible by rerunning the script.
- Quality 50 is tuned for photos and smears UI text: pass `--quality 80` for
  screenshots. Pass `--max-size 2560` for 48 MP phone originals — the site
  serves nothing wider than 1600 px. The committed set runs ~430 KB median,
  ~1.2 MB at the 90th percentile.
- `cvt2webp.py` needs `uv run --python 3.12`. The default interpreter here is
  3.14, `pi-heif` publishes no 3.14 wheel, and uv's fallback source build dies
  with `MSYS2 not found`. The failure is in dependency resolution, so it hits
  jpg and png input too, not just HEIC.
- `cvt2webp.py` skips `.webp` / `.gif` / `.avif` input. Move those by hand.
- `video.sh`'s `HEIGHT` is applied as `scale=$HEIGHT:-2`, so it sets the
  **width**, and defaults to 1920 — portrait footage gets upscaled. Pass
  `HEIGHT=1080` for portrait and verify with `ffprobe`. `hevc_nvenc` needs the
  NVIDIA GPU; `libx265 -crf 26 -preset slow` is the fallback.
- Originals left loose in the post root are invisible to every script: move
  them into `raw/` first. Only `img/`, `vid/`, `ext/` ship; only `raw/`,
  `unslimmed/`, `orig/`, `src/` are recognised scratch. Any other
  subdirectory is skipped and reported as a stray, so invent no new names.
- `BROKEN REFS` blocks the rebuild: something under `img/`, `vid/` or `ext/`
  is linked but absent. Usually a conversion that failed, a file still sitting
  in `raw/`, or a path you rewrote to the wrong extension. Fix the media or
  the path — never delete the user's link to make the check pass.
- `SHARED TAG` means two post directories resolve to one release tag and the
  script refused rather than overwrite the other post's media. Don't work
  around it — the second post needs its own longer tag.
- The release must exist *before* CI builds, or the post goes live with broken
  images.
- There is no local preview. `scripts/archive/local.bat` is kept only as a
  record of why. Review the deployed site.
- `video.sh` is bash: run it through Git Bash, not PowerShell. `uv`, `gh` and
  `ffmpeg` (NVENC, libx265, libsvtav1) are on PATH.

## Done when

```bash
uv run scripts/media_sync.py <post> --check -v   # everything in sync
git status --short                               # only index.md / meta.md
```

…and you committed nothing.
