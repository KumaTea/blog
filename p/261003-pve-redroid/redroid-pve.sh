#!/usr/bin/env bash
set -euo pipefail

# ---- settings: edit these ---------------------------------------------------
CTID=402
CT_NAME=redroid
IP=10.3.4.2/16
GW=10.3.4.1
DNS=10.3.4.1
BRIDGE=vmbr0
CORES=28
MEMORY=16384  # MiB
SWAP=16384  # MiB
DISK_GB=32  # size of the rootfs and of /data, each
ROOTFS_STORE=hd  # usually local-lvm
TPL_STORE=pd  # usually local
TRANSLATOR=ndk  # ndk = official image (Google libndk_translation)
                # houdini = erstt image (Intel libhoudini, Intel CPUs)
RENDER=/dev/dri/renderD128  # GPU render node; empty = software rendering
WIDTH=1920; HEIGHT=1080; DPI=320
DOCKER_IO=docker.1ms.run  # optional mirror for docker.io

# ---- derived ----------------------------------------------------------------
say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

case "$TRANSLATOR" in
  ndk)     IMAGE=docker://$DOCKER_IO/redroid/redroid:11.0.0-latest
           BRIDGE_LIB=libndk_translation.so ;;
  houdini) IMAGE=docker://$DOCKER_IO/erstt/redroid:11.0.0_houdini_ChromeOS
           BRIDGE_LIB=libhoudini.so ;;
  *)       die "TRANSLATOR must be ndk or houdini" ;;
esac
OCI=/dev/shm/redroid-oci:$TRANSLATOR
WORK=/dev/shm/redroid-rootfs-$CTID
TPL_NAME=redroid-11-$TRANSLATOR-rootfs.tar.gz
TPL_PATH=$(pvesm path "$TPL_STORE:vztmpl/$TPL_NAME") || die "storage $TPL_STORE cannot hold templates"

pct status "$CTID" >/dev/null 2>&1 && die "CT $CTID already exists"
[ -e "$WORK" ] && die "$WORK exists (leftover from a previous run?); remove it first"
[ -z "$RENDER" ] || [ -c "$RENDER" ] || die "$RENDER not found (set RENDER= for software rendering)"

# ---- 1. host prep (idempotent) ----------------------------------------------
say "1/7 host prep"
apt-get install -y -qq skopeo umoci jq >/dev/null

# binder: Android's IPC, provided by the host kernel through binderfs
echo binder_linux > /etc/modules-load.d/redroid.conf
echo 'options binder_linux devices=binder,hwbinder,vndbinder' > /etc/modprobe.d/redroid.conf
modprobe binder_linux devices=binder,hwbinder,vndbinder
grep -q binder /proc/filesystems || die "binderfs not available in this kernel"

# binfmt_misc is host-global for privileged containers. Remove ARM handlers that a
# redroid container may have registered (interpreter under /system/); leave others.
clean_binfmt() {
  [ -d /proc/sys/fs/binfmt_misc ] || return 0
  for f in /proc/sys/fs/binfmt_misc/*; do
    [ -f "$f" ] || continue
    case "$(basename "$f")" in register|status) continue ;; esac
    if grep -q '^interpreter /system/' "$f"; then
      echo "removing stray binfmt entry $(basename "$f")"
      echo -1 > "$f"
    fi
  done
}
clean_binfmt
BINFMT_BEFORE=$(ls /proc/sys/fs/binfmt_misc 2>/dev/null | sort | tr '\n' ' ' || true)

# ---- 2. pull and flatten ----------------------------------------------------
# PVE's own OCI import fails for privileged containers ("setgid(0): Invalid
# argument"), so merge the layers ourselves and give PVE a plain rootfs tarball.
say "2/7 pull $IMAGE and flatten"
skopeo copy "$IMAGE" "oci:$OCI"
CFG=$(skopeo inspect --config "oci:$OCI")
umoci raw unpack --image "$OCI" "$WORK"
[ -n "$(find "$WORK" -xdev -name "$BRIDGE_LIB")" ] || die "$BRIDGE_LIB not found in the image"

# ---- 3. patch the rootfs ----------------------------------------------------
say "3/7 patch rootfs"
mapfile -t RCS < <(find "$WORK" -xdev -type f -name '*.rc')
[ ${#RCS[@]} -gt 0 ] || die "no .rc files found in the rootfs"

# a) Never let Android mount the FUSE control fs: it would stack over the empty
#    tmpfs (step 5) and expose the host's FUSE connections to vold's abort.
grep -nE '^\s*mount\s+fusectl' "${RCS[@]}" || true
sed -i -E 's/^(\s*)(mount\s+fusectl.*)$/\1# \2/' "${RCS[@]}"

# b) No binfmt registration: it would land in the host's global table. Apps load
#    ARM .so files through the native bridge, which does not need binfmt.
grep -nE 'binfmt_misc/register|mount\s+binfmt_misc' "${RCS[@]}" || true
sed -i -E '/^\s*#/! s/^(\s*)(.*(binfmt_misc\/register|mount\s+binfmt_misc).*)$/\1# \2/' "${RCS[@]}"

# c) PVE drops CAP_SYS_MODULE; a service asking for it crash-loops (the Wi-Fi HAL).
grep -nE '^\s*capabilities\b.*SYS_MODULE' "${RCS[@]}" || true
sed -i -E '/^\s*capabilities\b/ s/ SYS_MODULE\b//g' "${RCS[@]}"

# ---- 4. pack template -------------------------------------------------------
say "4/7 pack template $TPL_PATH"
mkdir -p "$(dirname "$TPL_PATH")"
tar -C "$WORK" --numeric-owner --xattrs -czf "$TPL_PATH" .
rm -rf "$WORK"

# ---- 5. create container ----------------------------------------------------
say "5/7 create CT $CTID"
ARGS=(
  --hostname "$CT_NAME" --unprivileged 0 --ostype unmanaged --arch amd64
  --cores "$CORES" --memory "$MEMORY" --swap "$SWAP"
  --rootfs "$ROOTFS_STORE:$DISK_GB"
  --mp0 "$ROOTFS_STORE:$DISK_GB,mp=/data"
  --net0 "name=eth0,bridge=$BRIDGE,ip=$IP,gw=$GW,host-managed=1"
  --dev1 "/dev/fuse,mode=0666"          # vold mounts Android's internal storage via FUSE
)
if [ -n "$RENDER" ]; then ARGS+=(--dev0 "$RENDER,mode=0666"); fi
pct create "$CTID" "$TPL_STORE:vztmpl/$TPL_NAME" "${ARGS[@]}"

# Entrypoint = the image's /init + our boot args + the image's own args (ours win).
# In redroid, ro.* boot args override build.prop.
OURS=(
  androidboot.use_memfd=true             # no ashmem in current kernels
  "androidboot.redroid_width=$WIDTH"
  "androidboot.redroid_height=$HEIGHT"
  "androidboot.redroid_dpi=$DPI"
  androidboot.redroid_net_ndns=1
  "androidboot.redroid_net_dns1=$DNS"
  ro.enable.native.bridge.exec=0         # binfmt off (host-global)
  ro.enable.native.bridge.exec64=0
)
if [ -n "$RENDER" ]; then
  OURS+=(androidboot.redroid_gpu_mode=host "androidboot.redroid_gpu_node=$RENDER")
else
  OURS+=(androidboot.redroid_gpu_mode=guest)
fi
if [ "$TRANSLATOR" = houdini ]; then
  OURS+=(ro.dalvik.vm.native.bridge=libhoudini.so ro.dalvik.vm.isa.arm=x86 ro.dalvik.vm.isa.arm64=x86_64)
fi
ENTRY=$(jq -r '.config.Entrypoint // [] | join(" ")' <<<"$CFG")
[ -n "$ENTRY" ] || die "image has no Entrypoint"
mapfile -t IMGARGS < <(jq -r '.config.Cmd // [] | .[]' <<<"$CFG")
declare -A SET=(); for a in "${OURS[@]}"; do SET["${a%%=*}"]=1; done
KEEP=(); for a in "${IMGARGS[@]}"; do [[ -n "${SET["${a%%=*}"]:-}" ]] || KEEP+=("$a"); done
EP="$ENTRY ${OURS[*]} ${KEEP[*]}"
EP="${EP%"${EP##*[! ]}"}"
pct set "$CTID" --entrypoint "$EP"
echo "entrypoint: $EP"

cat >> "/etc/pve/lxc/$CTID.conf" <<'EOF'
lxc.apparmor.profile: unconfined
lxc.cgroup2.devices.allow: a
lxc.mount.entry: tmpfs dev/__properties__ tmpfs rw,nosuid,nodev,mode=0711,size=32M,create=dir 0 0
lxc.mount.entry: tmpfs sys/fs/fuse/connections tmpfs ro,nosuid,nodev,noexec,mode=0555 0 0
EOF
pct snapshot "$CTID" fresh

# ---- 6. start ---------------------------------------------------------------
say "6/7 start CT $CTID"
pct start "$CTID"
printf 'waiting for boot'
for _ in $(seq 90); do
  [ "$(pct exec "$CTID" -- /system/bin/getprop sys.boot_completed 2>/dev/null)" = 1 ] && break
  printf '.'; sleep 2
done
echo
[ "$(pct exec "$CTID" -- /system/bin/getprop sys.boot_completed 2>/dev/null)" = 1 ] \
  || die "no boot_completed after 3 min; check: dmesg -T | grep ' init: ' | tail -40"

# ---- 7. verify --------------------------------------------------------------
say "7/7 verify"
p() { pct exec "$CTID" -- /system/bin/getprop "$1"; }
echo "native bridge : $(p ro.dalvik.vm.native.bridge)   (want $BRIDGE_LIB)"
echo "abilist       : $(p ro.product.cpu.abilist)"
echo "fusectl view  : [$(pct exec "$CTID" -- /system/bin/ls /sys/fs/fuse/connections | tr '\n' ' ')]   (want empty)"
echo "storage FUSE  : $(pct exec "$CTID" -- /system/bin/grep ' fuse ' /proc/mounts | head -1)"

# Fire the call that once took down pmxcfs on purpose; it must be harmless now.
pct exec "$CTID" -- /system/bin/vdc volume abort_fuse >/dev/null 2>&1 || true
if ls /etc/pve >/dev/null 2>&1 && ls /var/lib/lxcfs/proc >/dev/null 2>&1; then
  echo "host FUSE     : OK after abort_fuse"
else
  echo "host FUSE     : BROKEN. Recover with:"
  echo "  lxc-stop -n $CTID --kill; systemctl stop pve-cluster; umount -l /etc/pve"
  echo "  systemctl start pve-cluster; systemctl restart pvedaemon pveproxy pvestatd pvescheduler lxcfs"
  exit 1
fi

BINFMT_AFTER=$(ls /proc/sys/fs/binfmt_misc 2>/dev/null | sort | tr '\n' ' ' || true)
if [ "$BINFMT_BEFORE" = "$BINFMT_AFTER" ]; then
  echo "host binfmt   : unchanged"
else
  echo "host binfmt   : CHANGED ($BINFMT_BEFORE -> $BINFMT_AFTER); cleaning"; clean_binfmt
fi

ADDR=${IP%/*}
cat <<EOF

Done: CT $CTID is up at $ADDR. Rollback point: pct rollback $CTID fresh
From another machine:
  adb connect $ADDR:5555
  adb -s $ADDR:5555 shell sm list-volumes     # want: emulated;0 mounted
  scrcpy -s $ADDR:5555 --no-audio
Stop it with 'pct stop $CTID', never 'adb reboot'.
EOF
