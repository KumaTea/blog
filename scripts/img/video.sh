#!/usr/bin/env bash
# Shrink a video for the blog: 1080p, NVENC HEVC, ~2.5 Mbit/s.
#
#     bash scripts/img/video.sh input.mp4                 # -> input.out.mp4
#     bash scripts/img/video.sh input.mp4 vid/skyline.mp4
#
# Drop the result in the post's vid/ directory and reference it with
# {{< video src="vid/skyline.mp4" >}}.

set -e

IN="${1:?usage: video.sh <input> [output]}"
OUT="${2:-${IN%.*}.out.mp4}"
HEIGHT="${HEIGHT:-1920}"
VBR="${VBR:-2500k}"
ABR="${ABR:-64k}"

ffmpeg -hwaccel auto -i "$IN" -c:v hevc_nvenc -vf "scale=$HEIGHT:-2" \
    -b:v "$VBR" -b:a "$ABR" "$OUT"

echo "$IN -> $OUT"
ls -lh "$IN" "$OUT" | awk '{print $5, $9}'
