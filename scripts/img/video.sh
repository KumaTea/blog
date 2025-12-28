ffmpeg -hwaccel auto -i input.mp4 -c:v hevc_nvenc -vf "scale=1920:-2" -b:v 2500k -b:a 64k out.mp4
