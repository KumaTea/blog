# Running Redroid in a Proxmox VE LXC Container (with an Intel DG1 GPU)

Oct 2, 2026 · @KumaTea

## Summary

Redroid (Android 11) runs as a native, `pct`-managed LXC container on Proxmox VE 9.2, with GPU rendering on an Intel DG1 that other containers keep sharing. No VM and no Docker. The only public attempt I could find, a [2024 Proxmox forum thread](https://forum.proxmox.com/threads/running-redroid-directly-in-pve-lxc.147298/), stalled at the conversion to a PVE container.

- **Works:** boot, GPU-accelerated rendering through Mesa on the DG1, ADB, scrcpy, Android's internal storage, and clean restarts that leave the host untouched.
- **Doesn't work:** my actual goal, Arknights for 24/7 [MAA](https://github.com/MaaAssistantArknights/MaaAssistantArknights) automation. The game is ARM-only, so on x86 it runs through an ARM translator. It crashed at the same moment under both Google's `libndk_translation` and Intel's `libhoudini`: while it was starting its anti-cheat and anti-fraud SDKs.

The setup is still useful for any app that runs on x86 Android or survives translation. One warning up front: the container is privileged with AppArmor off, so treat it as root on the host. One of my early mistakes took down `/etc/pve` (the journey section has the details).

## Requirements

The hard requirements are a PVE 9.1+ host with binderfs in its kernel and pve-container 6.1.13 or newer. Everything else is what I happened to test with.

| Component | Tested with | Why it matters |
| --- | --- | --- |
| Proxmox VE | pve-manager 9.2.3 | 9.1+ adds the `entrypoint` and `host-managed` container options, which came with OCI support |
| Kernel | 7.0.12-1-pve | Needs the `binder_linux` module with binderfs. Ashmem is gone, so Android uses memfd instead |
| pve-container | 6.1.14 | 6.1.13+ [fixes device passthrough](https://git.proxmox.com/?p=pve-container.git;a=commitdiff;h=5fd625696475b8e6821280859512900293e53b2b) into privileged containers with `ostype: unmanaged` |
| CPU | Intel Xeon E5-2680 v4 (Broadwell) | Any x86-64 |
| GPU (optional) | Intel DG1, PCI ID 8086:4908, `i915` driver | Any render node the image's Mesa supports. Keep `i915`: Android 11's Mesa predates `xe` |
| Storage | ZFS | Any. Cheap snapshots make experiments painless |
| Host packages | `skopeo`, `umoci`, `jq` | Pull and flatten the image |
| Client | `adb` (platform-tools), scrcpy 3.3.4 | Connect and view |
| Image | `redroid/redroid:11.0.0-latest` | Or `erstt/redroid:11.0.0_houdini_ChromeOS` for Intel's ARM translator |

Check the host before you start:

```
pveversion -v | grep -E 'pve-manager|pve-container|running kernel'
modprobe binder_linux devices=binder,hwbinder,vndbinder && grep binder /proc/filesystems   # want: nodev binder
ls -l /dev/dri/by-path/          # find your GPU's renderD node
```

## Quick start

One script does everything: it prepares the host, pulls and flattens the image, patches the Android rootfs, creates the container, boots it, and checks that the host is safe. It only adds things; it never deletes a container or volume.

1. Save it on the PVE host as `redroid-pve.sh` and edit the settings block (container ID, IP, storage names, GPU node).
2. Run `bash redroid-pve.sh` as root. The first run downloads the image and unpacks about 2 GB under `/root`.
3. When it prints `Done`, connect from another machine with `adb connect <ip>:5555` and `scrcpy -s <ip>:5555 --no-audio`.

`TRANSLATOR=ndk` uses the official image with Google's ARM translator. `TRANSLATOR=houdini` uses a community image with Intel's translator, which needs an Intel CPU. Leave `RENDER` empty for software rendering.

```bash
#!/usr/bin/env bash
# redroid-pve.sh: build and start redroid (Android 11) as a privileged LXC container
# on Proxmox VE 9.1+. Additive only: it never deletes a container or volume.
set -euo pipefail

# ---- settings: edit these ---------------------------------------------------
CTID=402
CT_NAME=redroid
IP=192.168.1.50/24
GW=192.168.1.1
DNS=192.168.1.1
BRIDGE=vmbr0
CORES=4
MEMORY=8192                  # MiB
SWAP=8192                    # MiB
DISK_GB=32                   # size of the rootfs and of /data, each
ROOTFS_STORE=local-lvm       # storage for the container volumes
TPL_STORE=local              # storage with the "vztmpl" content type
TRANSLATOR=ndk               # ndk = official image (Google libndk_translation)
                             # houdini = erstt image (Intel libhoudini, Intel CPUs)
RENDER=/dev/dri/renderD128   # GPU render node; empty = software rendering
WIDTH=1920; HEIGHT=1080; DPI=320

# ---- derived ----------------------------------------------------------------
say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

case "$TRANSLATOR" in
  ndk)     IMAGE=docker://docker.io/redroid/redroid:11.0.0-latest
           BRIDGE_LIB=libndk_translation.so ;;
  houdini) IMAGE=docker://docker.io/erstt/redroid:11.0.0_houdini_ChromeOS
           BRIDGE_LIB=libhoudini.so ;;
  *)       die "TRANSLATOR must be ndk or houdini" ;;
esac
OCI=/root/redroid-oci:$TRANSLATOR
WORK=/root/redroid-rootfs-$CTID
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
```

Both translators booted on my host with these exact settings. The official image with `libndk_translation` I set up by hand, and the houdini image with an earlier, single-purpose version of this script. This generalized version (selectable translator, optional GPU) passes `bash -n` and shellcheck, but I haven't run it end-to-end yet. If it trips, `pct rollback <ctid> fresh` gets you back to the pre-boot state.

## What each setting does

Redroid shares the host kernel, so most of these settings are about giving Android what it needs without letting it reach host-wide state. Each row exists because something broke without it.

| Setting | Where | Why |
| --- | --- | --- |
| `binder_linux` loaded with `devices=binder,hwbinder,vndbinder` | Host | Android's IPC. The PVE kernel ships it; the script makes it load at boot |
| `--unprivileged 0` | `pct create` | Android's init mounts binderfs, cgroups and tmpfs itself and needs real root |
| `ostype: unmanaged` | Config | Otherwise PVE edits `/etc/hostname` and network files, which Android doesn't have |
| `entrypoint: /init qemu=1 androidboot.hardware=redroid …` | Config | Android's init is `/init` and takes `androidboot.*` arguments; PVE defaults to `/sbin/init` |
| `androidboot.use_memfd=true` | Entrypoint | Current kernels have no ashmem |
| `androidboot.redroid_gpu_mode=host`, `…gpu_node=` | Entrypoint | Render through Mesa on the GPU instead of SwiftShader |
| `ro.enable.native.bridge.exec=0`, `exec64=0` | Entrypoint | Stops the translator registering ARM handlers in the host's global binfmt table |
| `net0: …,ip=<static>,host-managed=1` | Config | The host configures eth0 before `/init` runs, as Docker would |
| `mp0: …,mp=/data` | Config | App data lives on its own volume: survives image swaps, gets snapshots and backups |
| `dev0: /dev/dri/renderD128,mode=0666` | Config | The GPU. 0666 because Android's graphics services run as Android UIDs; the host's node stays 0660. Needs pve-container 6.1.13+ |
| `dev1: /dev/fuse,mode=0666` | Config | vold mounts Android's internal storage over FUSE. Without it, apps log `Failed to find mounted volume for /storage/emulated/0/…` |
| `lxc.apparmor.profile: unconfined` | Raw LXC | PVE's AppArmor profile blocks the mounts Android's init makes |
| `lxc.cgroup2.devices.allow: a` | Raw LXC | binderfs nodes get a dynamic major number that isn't on PVE's device allow-list: the node exists, but opening it fails with EPERM |
| `lxc.mount.entry: tmpfs dev/__properties__ … size=32M` | Raw LXC | Android's property files need several MB; LXC's `/dev` is about 500 KB. Without it, init dies in `prop_area::map_prop_area_rw` and the container boot-loops |
| `lxc.mount.entry: tmpfs sys/fs/fuse/connections … ro` | Raw LXC | Hides the host's FUSE control files. Without it, Android aborts the host's FUSE mounts, including `/etc/pve` |
| Comment out `mount fusectl` | Rootfs patch | Keeps Android from mounting the real control files over that cover. Redroid 11 already removed the line; the check stays for other images |
| Comment out binfmt registration | Rootfs patch | Same host-global table as above |
| Remove `SYS_MODULE` from `capabilities` lines | Rootfs patch | PVE drops CAP\_SYS\_MODULE, so services asking for it (the Wi-Fi HAL) crash-loop and flood the host journal |

I deliberately left out the other half of Docker's `--privileged`: an empty `lxc.cap.drop` and `lxc.mount.auto: proc:rw sys:rw`. Many sysctls aren't namespaced, so a writable `/proc/sys` would let Android change settings for the whole host. With PVE's defaults those writes fail harmlessly.

One more PVE quirk: raw `lxc.*` keys must be on PVE's whitelist (`grep -n autodev /usr/share/perl5/PVE/LXC/Config.pm` shows the relevant part). Redroid's own LXC recipe uses `lxc.autodev.tmpfs.size`, which isn't on it. An unknown key doesn't stop the container, but it makes PVE's hook scripts fail, including the one that attaches the network.

## Using it

You use it like any network Android device: ADB on port 5555, scrcpy for the screen, and `pct stop` to shut it down.

- **View:** `scrcpy -s <ip>:5555 --no-audio`. Redroid has no Opus encoder, so scrcpy's default audio stream fails and takes the session down.
- **Shell:** one-shot `adb shell <cmd>` works. Interactive `adb shell` fails with `child failed to open pseudo-term slave /dev/pts/N`, because Android mounts its own `/dev/pts` over LXC's. From the host, `pct exec <ctid> -- /system/bin/<cmd>` works; use full paths.
- **Logs:** Android's init writes to the host's kernel log (`dmesg -T | grep ' init: '`). Everything else is in `adb logcat`. Native crash dumps land in `/data/tombstones`, which on the host is the `mp0` volume. An app with its own crash reporter may exit without leaving a tombstone.
- **Stop:** `pct stop <ctid>`, never `adb reboot`. Android's shutdown and every zygote restart call `vdc volume abort_fuse`; the FUSE cover makes that harmless, but don't test it by accident.
- **Resolution:** `redroid_width` and `redroid_height` set the render size. DPI only scales the UI: 240 gives tiny menus at 1080p, 320 to 420 is comfortable.
- **Harmless noise:** `ro.build.fingerprint has a value with length 102` (redroid's fingerprint is over Android's 91-character limit) and `Unexpected CPU variant for X86`.

### Security

The container is privileged, unconfined and allowed every device, so anything running as root inside it is effectively root on the host. Only run images you can inspect. Before using the houdini image, I checked that it adds exactly one layer (`COPY houdini /`) on top of the official one. Never expose port 5555 outside your LAN: ADB here asks for no authorization, and this userdebug build allows adb root.

### DG1 on an older platform: AER log flood

This only applies to a DG1 behind a root port without LTR support, such as Broadwell's. Once the GPU was in use, every touch in scrcpy produced a burst like this:

```
pcieport 0000:00:03.0: PCIe Bus Error: severity=Uncorrectable (Non-Fatal), type=Transaction Layer, (Requester ID)
pcieport 0000:00:03.0:    [20] UnsupReq               (First)
pcieport 0000:00:03.0: AER:   TLP Header: 0x34008000 0x02000010 0x00000000 0x80008000
```

The TLP header decodes to a PCIe *message* (`0x34`) from `02:00.0`, the PCIe switch on the DG1 card, with message code `0x10`: Latency Tolerance Reporting. The GPU wakes up and announces "I need full speed". The root port reports `LTR-` in `DevCap2`, so it rejects each message as an Unsupported Request. Nothing fails, but MAA-style automation tapping all day would fill the journal.

Clearing the LTR enable bit on the card changed nothing; the card sends the messages anyway. Masking Unsupported Request on that one root port did:

```
cat > /etc/systemd/system/dg1-aer-mask.service <<'EOF'
[Unit]
Description=Mask PCIe Unsupported Request on root port 00:03.0 (DG1 LTR noise)

[Service]
Type=oneshot
ExecStart=/usr/bin/setpci -s 00:03.0 ECAP_AER+0x08.L=0x00100000:0x00100000

[Install]
WantedBy=multi-user.target
EOF
systemctl enable --now dg1-aer-mask.service
```

Replace `00:03.0` with your GPU's root port. The trade-off is that this port stops reporting any Unsupported Request, which is far narrower than `pci=noaer`. And stay on `i915` rather than `xe` for the DG1: Android 11's Mesa can't talk to `xe`, and `xe` doesn't fix the LTR messages anyway.

## The journey

It took eight obstacles to get from an OCI image to a GPU-accelerated container, and one of them took the host's cluster filesystem down with it.

The goal was modest. I've played Arknights since 2019 and let [MAA](https://github.com/MaaAssistantArknights/MaaAssistantArknights) play the dailies. My PC has 16 GB of RAM, which can't hold two games at once, so I wanted the Android side on my Proxmox host, available 24/7. It had to be a container, not a VM: a VM would need the DG1 passed through exclusively, taking it away from my immich container, while containers can share the GPU's render node.

Redroid fits because it isn't an emulator. It runs Android's userspace directly on the host kernel, the way a Debian LXC runs Debian's. That also means every hard requirement lands on the host.

| # | Symptom | Cause | Fix |
| --- | --- | --- | --- |
| 1 | `pct create` from the OCI image: `setgid(0): Invalid argument` | PVE's OCI importer fails for privileged containers. The image declares no volumes, so the exact step is still unknown to me | Flatten the image with `skopeo` + `umoci` and give PVE a plain rootfs tarball |
| 2 | `unable to parse config: lxc.autodev.tmpfs.size` | Raw key from redroid's LXC recipe isn't on PVE's whitelist; it also broke the hook that attaches the network | Drop it |
| 3 | Boot loop, `InitFatalReboot` in `prop_area::map_prop_area_rw` | Android's property files don't fit LXC's small `/dev` | A 32 MB tmpfs at `dev/__properties__` |
| 4 | Boots, but renders with SwiftShader; `dev0` says the render node doesn't exist | pve-container 6.1.10 bug for privileged containers with `ostype: unmanaged` | Upgrade to 6.1.14 |
| 5 | `/etc/pve` gone, SSH asks for a password | Android aborted every FUSE connection on the host (below) | Read-only tmpfs over `sys/fs/fuse/connections` |
| 6 | Journal flooded with `cap_set_proc(77824) failed` | 77824 = 0x13000 = NET\_ADMIN + NET\_RAW + SYS\_MODULE; PVE drops SYS\_MODULE, so the Wi-Fi HAL crash-loops | Remove SYS\_MODULE from its `capabilities` line |
| 7 | Every touch logs PCIe AER errors | DG1 sends LTR messages the Broadwell root port doesn't support | Mask Unsupported Request on that port |
| 8 | `Failed to find mounted volume for /storage/emulated/0/…` | No `/dev/fuse` in LXC's minimal `/dev` | Pass `/dev/fuse` through |

The first boot that rendered on the DG1 felt like the finish line. `intel_gpu_top` showed activity while I swiped around in scrcpy.

### The day Android unplugged /etc/pve

Number 5 deserves its own story, because a container broke the host from the inside.

After one restart, `/etc/pve` answered `Transport endpoint is not connected`, every `pct` command failed with `ipcc_send_rec failed: Connection refused`, and SSH asked for a password. `/etc/pve` is a FUSE filesystem served by `pmxcfs`, and on PVE `/root/.ssh/authorized_keys` is a symlink into `/etc/pve/priv`. So when that one mount went away, my SSH keys went with it.

The cause is a function in Android's storage daemon, [`AbortFuseConnections()`](https://android.googlesource.com/platform/system/vold/+/23c0445%5E%21/). It walks `/sys/fs/fuse/connections` and writes `1` into every connection's `abort` file, so shutdown doesn't hang on a dead FUSE daemon. On a phone, every connection there belongs to Android. In a container, LXC bind-mounts the host's directory, and privileged root can write to it. Android aborted `pmxcfs` and `lxcfs` too.

The trigger in my logs was a chain of restarts. I had switched to GPU host mode before the render node passthrough worked, so the graphics allocator exited. SurfaceFlinger aborted, its restart hook restarted zygote, and zygote's restart hook runs `vdc volume abort_fuse`. Android's normal shutdown runs the same command, so `adb reboot` does it too.

&#91;embedded content: outage chain · container to host\]

Everything inside the container is ordinary Android behavior; the bind-mounted directory is what let it reach the host.

Recovery needed no reboot. The data under `/etc/pve` lives in `/var/lib/pve-cluster/config.db` and was untouched:

```
lxc-stop -n <ctid> --kill        # pct needs pmxcfs; stop the culprit first
systemctl stop pve-cluster
umount -l /etc/pve
systemctl start pve-cluster
systemctl restart pvedaemon pveproxy pvestatd pvescheduler
systemctl restart lxcfs          # then reboot the other containers to refresh their /proc views
```

The fix is an empty, read-only tmpfs over `sys/fs/fuse/connections` inside the container. Android sees nothing to abort. I also confirmed that redroid's `init.rc` doesn't mount the FUSE control filesystem itself; only the comment `# Mount fusectl` is left. Then I fired the abort deliberately with `vdc volume abort_fuse` and checked that `/etc/pve` survived. The setup script repeats that test on every new container.

## Why Arknights still doesn't run

Arknights crashed at the same moment under both ARM translators: about 0.3 s after its update check returns, while the screen shows 正在加载网络配置 and the game starts its protection SDKs. Those are Tencent's ACE anti-cheat (`com.ace.gshell`) and Shumei's anti-fraud SDK (`com.ishumei.smantifraud`). The network was fine: the update server answered `{"code":0,"version":"77.0.0",…}` just before each crash.

The game itself translates well. Unity, which is ARM code, ran all the way into its hot-update workflow. The catch is that Arknights [dropped its x86 libraries about a year ago](https://ngabbs.com/read.php?tid=43752188), so on an x86 host every line of its native code goes through a translator. MAA's docs still say redroid 11 runs the game, but that note predates the change.

| Image | Translator | Crashing thread | Signal |
| --- | --- | --- | --- |
| `redroid/redroid:11.0.0-latest` | Google `libndk_translation` | `sm-thread-http2` (Shumei) | SIGSEGV, `SEGV_ACCERR`, fault address = instruction pointer |
| `erstt/redroid:11.0.0_houdini_ChromeOS` | Intel `libhoudini` 11.0.1f | `UnityMain` | SIGILL, reported by the game's own crash handler |

### What the tombstone shows

Under `libndk_translation`, the crashing thread was running Shumei's ARM code, which called Java's `getFilesDir()` through the translator's JNI trampoline. That reached the host's compiled x86 code for `java.io.File.exists`, and the CPU refused to execute it. The memory map shows why:

```
70c83000-70d50fff r-x  boot.oat        code
70d51000-70d51fff rw-  boot.oat        one page of code, no longer executable
70d52000-70f46fff r-x  boot.oat        code
```

I scanned the whole map: six pages of framework code had lost their execute bit, one in `boot.oat` and five in `boot-framework.oat`. Android's runtime maps that code executable once and never changes it, so something else in the process did.

### My best explanation

This is an inference that the evidence supports but doesn't prove. The protection code believes it runs in an ARM process, so it treats the framework's compiled methods as ARM code and changes their page permissions. That's typical of hook detection, and `File.exists` is the first method any root detector looks at.

Every `mprotect` call from translated code passes through the translator, and a translator never lets guest code run directly on the host CPU. As I understand the design, when ARM code asks for a page to be executable, `libndk_translation` keeps the host page non-executable and tracks it itself. The page becomes plain data to the real CPU, and the next `File.exists` call crashes.

Under `libhoudini` the same moment produced SIGILL instead. That fits a translator that lets the permission change through: the x86 CPU then executes whatever was written there. I couldn't confirm it, because the game's crash reporter caught the signal and exited without a tombstone. (Houdini also logged that it couldn't load `libart.so` because of Android 11's linker namespaces, so it ran without part of its integration.)

### Why I stopped

Two different translators failing at the same SDK start-up points at the SDKs, not the translators. PC emulators work because their translators and environments are the ones these SDKs are tested against, and many SDKs leave the framework alone when they recognize them. I didn't try the remaining candidate, an Android 12 image with a newer `libndk_translation`, because nothing about it changes what the SDKs see.

Making the container impersonate a known emulator might change the SDKs' behavior, but that means deceiving the anti-cheat, and it's the fastest way to get an account flagged. That's where I drew the line.

## Alternatives and cleanup

For an ARM-only game with anti-cheat, the clean fix is to stop translating: run it on real ARM hardware and keep only the automation on PVE.

- **A spare Android phone.** Real ARM, no translation, and MAA officially supports physical devices over network ADB. `maa-cli` in an LXC on PVE drives it 24/7. Force MAA's 16:9 requirement with `adb shell wm size 1920x1080`, and set a charge limit so the battery doesn't swell from being plugged in permanently.
- **Native ARM redroid** on an RK3588 board or an Ampere cloud VM. Also no translation, but GPU support on Mali is fiddly, and anti-fraud SDKs may distrust a container or cloud environment.
- **A scheduled emulator on the PC.** MAA can start MuMu, run the dailies and close it again during hours the PC is idle. It isn't 24/7, but dailies only need a few short runs a day.

To remove everything this post sets up:

```
pct stop <ctid>; pct destroy <ctid> --purge
pveam remove <tpl-store>:vztmpl/redroid-11-ndk-rootfs.tar.gz       # and/or the houdini one
rm -rf /root/redroid-oci
rm /etc/modules-load.d/redroid.conf /etc/modprobe.d/redroid.conf
```

Keep the `dg1-aer-mask` service if the DG1 stays in use for something else; in my case it still accelerates immich.

## References

- [redroid-doc](https://github.com/remote-android/redroid-doc): images, boot parameters, native bridge notes
- [redroid LXC deploy recipe](https://github.com/remote-android/redroid-doc/blob/master/deploy/lxc.md): the plain-LXC starting point
- [Running Redroid directly in PVE LXC](https://forum.proxmox.com/threads/running-redroid-directly-in-pve-lxc.147298/): the 2024 Proxmox forum attempt
- [Proxmox Container Toolkit docs](https://pve.proxmox.com/pve-docs/chapter-pct.html): `entrypoint`, `host-managed`, `devN`, raw LXC keys
- [pve-container: fix #7667, device passthrough](https://git.proxmox.com/?p=pve-container.git;a=commitdiff;h=5fd625696475b8e6821280859512900293e53b2b): the `dev0` bug in privileged containers
- [Proxmox VE 9.1 with OCI-based containers (heise)](https://heise.de/-11086586): OCI image support, tech preview
- [AOSP vold: Support aborting FUSE connections](https://android.googlesource.com/platform/system/vold/+/23c0445%5E%21/): `AbortFuseConnections()`
- [AOSP init.rc](https://android.googlesource.com/platform/system/core/+/HEAD/rootdir/init.rc): where stock Android mounts `fusectl`
- [MAA: Linux emulators and containers](https://docs.maa.plus/zh-cn/manual/device/linux.html) and [maa-cli configuration](https://docs.maa.plus/zh-cn/manual/cli/config.html)
- [erstt/redroid on Docker Hub](https://hub.docker.com/r/erstt/redroid) and [on GitHub](https://github.com/ERSTT/redroid): images with libhoudini and newer libndk\_translation
- [ayasa520/redroid-script](https://github.com/ayasa520/redroid-script): adds GApps, Magisk and libndk to redroid
- [Phoronix: DG1 drops force\_probe](https://phoronix.com/news/Intel-DG1-Linux-Force-Probe) and [DG1 support in Mesa 20.2](https://www.phoronix.com/news/Intel-DG1-In-Mesa-20.2)
- [NGA: Arknights drops x86 support](https://ngabbs.com/read.php?tid=43752188)
