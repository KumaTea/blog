#!/usr/bin/env bash


set -e

echo "extracting hugo directory..."
mv hugo/* .
rm -rf hugo

# echo "preparing deps..."
# sudo bash scripts/deps.sh

echo "add theme..."
mkdir -p themes
sed -i 's/themes//g' .gitignore
# git submodule add https://github.com/CaiJimmy/hugo-theme-stack themes/stack
git submodule add https://github.com/KumaTea/hugo-theme-stack themes/stack
# git -C themes/stack apply scripts/patches/*.patch
# git -C themes/stack apply ../../scripts/patches/*.patch
# cd themes/stack/
# git apply ../../scripts/patches/stack/*.patch
# cd ../../

echo "setting post modified date..."
python3 scripts/meta.py

echo "preparing media for posts..."
python3 scripts/media.py

echo "adding slugs..."
python3 scripts/slug.py

echo "moving posts..."
mkdir -p content
cp -a posts content/

echo "adding layouts for search..."
mkdir -p layouts
cp -a themes/stack/layouts/page layouts/pages

echo "adding custom html head"
SRC="./static/head.html"
DST="./themes/stack/layouts/_partials/head/custom.html"
cat $SRC >> $DST
