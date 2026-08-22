import os
import logging


# A post's URL comes from its `slug`, and the slug is just the directory name:
#
#     posts/251020-wazzup-beijing  ->  /p/251020-wazzup-beijing/
#
# The front matter in meta.md deliberately leaves it out, so it gets added
# here at build time, right before the posts are copied into content/.

logging.basicConfig(level=logging.INFO)

pwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
posts_path = os.path.join(pwd, 'posts')


def add_slug(name):
    post_file = os.path.join(posts_path, name, 'index.md')
    if not os.path.isfile(post_file):
        return logging.warning(f'[slug]\t{name} has no index.md, skipped')

    slug = name.lower().replace(' ', '-')
    with open(post_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    if not lines or lines[0].strip() != '---':
        return logging.warning(f'[slug]\t{name} has no front matter, skipped')

    # Locate the front matter block rather than assuming a fixed line number,
    # so reordering a meta.md field cannot put the slug in the post body.
    end = next((i for i, line in enumerate(lines[1:], start=1)
                if line.strip() == '---'), None)
    if end is None:
        return logging.warning(f'[slug]\t{name} front matter unclosed, skipped')

    block = lines[1:end]
    if any(line.startswith('slug:') for line in block):
        return logging.info(f'[slug]\t{name} already has a slug, kept')

    title = next((i for i, line in enumerate(block) if line.startswith('title:')),
                 None)
    at = 1 + (title + 1 if title is not None else 0)
    lines.insert(at, f'slug: {slug}\n')

    with open(post_file, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    return logging.info(f'[slug]\t{name} slug added')


if __name__ == '__main__':
    for i in sorted(os.listdir(posts_path)):
        if os.path.isdir(os.path.join(posts_path, i)):
            add_slug(i)
