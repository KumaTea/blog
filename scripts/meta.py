import os
import logging
import subprocess
from datetime import datetime, timezone, timedelta

# timezone is UTC+8


logging.basicConfig(level=logging.INFO)

pwd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
posts_path = os.path.join(pwd, 'posts')
# about_page_path = 'content/pages/about/index.md'
about_page_path = os.path.join(pwd, 'content', 'pages', 'about', 'index.md')
git_date_fmt = '%a %b %d %H:%M:%S %Y %z'


def git_last_commit_date(*paths):
    """Committer date of the last commit touching paths, as a git-format str.

    -C pins git to the repository this script lives in, so the result does not
    depend on the directory the build happens to be launched from.
    """
    out = subprocess.check_output(
        ['git', '-C', pwd, 'log', '-1', '--format=%cd', *paths])
    return out.decode('utf-8').strip()
preferred_date_fmt = '%Y-%m-%d %H:%M:%S'
metadata_date_fmt = '%Y-%m-%d %H:%M:%S%z'


def set_about_info():
    """
    最后文章 LAST_POST_DATE
    最后更新 COMMIT_DATE
    构建日期 BUILD_DATE
    """
    # git log -1 --format=%cd posts
    # Sun Jun 4 02:42:49 2023 +0800
    git_last_post_date = git_last_commit_date(posts_path)
    last_post_date = datetime.strptime(
        git_last_post_date, git_date_fmt)
    last_post_date = last_post_date.astimezone(timezone(timedelta(hours=8)))
    last_post_date_str = last_post_date.strftime(preferred_date_fmt)

    git_commit_date = git_last_commit_date()
    commit_date = datetime.strptime(
        git_commit_date, git_date_fmt)
    commit_date = commit_date.astimezone(timezone(timedelta(hours=8)))
    commit_date_str = commit_date.strftime(preferred_date_fmt)

    # build_date = datetime.now()
    # convert to UTC+8
    build_date = datetime.now(timezone(timedelta(hours=8)))
    build_date_str = build_date.strftime(preferred_date_fmt)

    with open(about_page_path, 'r', encoding='utf-8') as f:
        about_page = f.read()
    about_page = about_page.replace('LAST_POST_DATE', last_post_date_str)
    about_page = about_page.replace('COMMIT_DATE', commit_date_str)
    about_page = about_page.replace('BUILD_DATE', build_date_str)
    with open(about_page_path, 'w', encoding='utf-8') as f:
        f.write(about_page)

    return logging.info('[meta]\tset about info')


def add_metadata_to_post(post_path):
    if os.path.isfile(os.path.join(posts_path, post_path, 'meta.md')):
        with open(os.path.join(posts_path, post_path, 'index.md'), 'r', encoding='utf-8') as f:
            post_text = f.read()
        with open(os.path.join(posts_path, post_path, 'meta.md'), 'r', encoding='utf-8') as f:
            meta_text = f.read()
        while meta_text.endswith('\n'):
            meta_text = meta_text[:-1]
        post_text = meta_text + '\n\n' + post_text
        with open(os.path.join(posts_path, post_path, 'index.md'), 'w', encoding='utf-8') as f:
            f.write(post_text)
        return logging.info(f'[meta]\t{post_path} add metadata')
    else:
        return None


def set_post_modified_date(post_path):
    post_date_str = post_path.split('-')[0]
    post_date = datetime.strptime(post_date_str, '%y%m%d').astimezone(timezone(timedelta(hours=8)))

    post_text_path = os.path.join(posts_path, post_path, 'index.md')
    # Merge meta.md in first: the front matter has to be there whether or not
    # a lastmod ends up being added below.
    add_metadata_to_post(post_path)

    git_commit_date = git_last_commit_date(post_text_path)
    if not git_commit_date:
        # never committed: a draft added since the last commit
        return logging.info(f'[date]\t{post_path} not in git yet, no lastmod')
    commit_date = datetime.strptime(
        git_commit_date, git_date_fmt).astimezone(timezone(timedelta(hours=8)))

    if post_date.day != commit_date.day or post_date.month != commit_date.month or post_date.year != commit_date.year:
        logging.info(f'[date]\t{post_path} modified date changed')
        # 2023-06-03T15:00:00+0800
        post_date_str = commit_date.strftime(metadata_date_fmt)

        with open(post_text_path, 'r', encoding='utf-8') as f:
            post_text = f.read()
        # insert `lastmod` next to the `date` line
        date_info_line = ''
        for line in post_text.split('\n'):
            if line.startswith('date:'):
                date_info_line = line
                break
        if not date_info_line:
            raise RuntimeError(f'[date]\t{post_path} date info not found')
        post_text = post_text.replace(date_info_line, date_info_line + f'\nlastmod: "{post_date_str}"')
        with open(post_text_path, 'w', encoding='utf-8') as f:
            f.write(post_text)
    else:
        return None


def list_posts():
    return sorted(
        d for d in os.listdir(posts_path)
        if os.path.isdir(os.path.join(posts_path, d))
        and os.path.isfile(os.path.join(posts_path, d, 'index.md'))
    )


def set_posts_modified_date():
    for post_path in list_posts():
        set_post_modified_date(post_path)
    return logging.info('[date]\tset posts modified date')


def remove_meta_file():
    for post_path in list_posts():
        meta_file_path = os.path.join(posts_path, post_path, 'meta.md')
        if os.path.isfile(meta_file_path):
            os.remove(meta_file_path)
            logging.info(f'[meta]\t{post_path} remove meta file')


if __name__ == '__main__':
    set_about_info()
    set_posts_modified_date()
    remove_meta_file()
