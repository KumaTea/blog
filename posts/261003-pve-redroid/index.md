# Proxmox VE LXC 容器运行 Redroid

![AIDA64](img/redroid-specs.webp)

## 概要

[Redroid](https://hub.docker.com/r/redroid/redroid) 是一个在 Docker 上运行 Android 的项目。

我想在PVE上通过LXC运行 Redroid，但网上没有现成的教程，找到唯一相关的是
[一篇2024年论坛帖子](https://forum.proxmox.com/threads/running-redroid-directly-in-pve-lxc.147298/)，
卡在把镜像转换为 PVE 容器这一步。

在 Claude 的帮助下，我成功实现了容器内启动以及使用显卡加速（不过初始目标运行明日方舟没有成功，无法通过反作弊SDK）。显卡为 **Intel DG1**，如果不需要，可能需要对脚本作相应修改。

![Intel GPU Top](img/redroid-intel.webp)

如果你想看 Claude 写的英文完整版，[请点击此处](https://github.com/KumaTea/blog/blob/main/posts/261003-pve-redroid/claude.md)。

## 环境要求

* PVE 9.1+
  * 9.1+ 加入了 `entrypoint` 和 `host-managed` 容器选项
* 内核提供 `binderfs`
* `pve-container` 6.1.13+
  * 6.1.13+ [修复了 privileged 容器设备直通](https://git.proxmox.com/?p=pve-container.git;a=commitdiff;h=5fd625696475b8e6821280859512900293e53b2b)
* `skopeo`, `umoci`, `jq`

```shell
pveversion -v | grep -E 'pve-manager|pve-container|running kernel'
modprobe binder_linux devices=binder,hwbinder,vndbinder && grep binder /proc/filesystems
# 输出 nodev binder
```

## 快速开始

1. 下载[脚本](https://blog.kmtea.eu/posts/261003-pve-redroid/redroid-pve.sh)，编辑最上面的配置
2. `sudo bash redroid-pve.sh`, 解包约 2 GB 数据
3. 运行 `adb connect <ip>:5555` 和 `scrcpy -s <ip>:5555 --no-audio` 连接

`TRANSLATOR=ndk` 使用搭载 Google ARM 转译器的官方镜像，
`TRANSLATOR=houdini` 使用社区镜像，需要 Intel CPU。
`RENDER` 留空使用软件渲染。

## 使用方式

- **操作**：`scrcpy -s <ip>:5555 --no-audio`
  - 不带 `--no-audio` 会失败
- **终端**：单次执行 `adb shell <cmd>`
  - `adb shell` 不能用
- **日志**：`adb logcat`
- **关机**：`adb shell reboot -p` 或者 `pct stop <ctid>`

### 日志刷屏

如果遇到日志刷屏：

```
pcieport 0000:00:03.0: PCIe Bus Error: severity=Uncorrectable (Non-Fatal), type=Transaction Layer, (Requester ID)
pcieport 0000:00:03.0:    [20] UnsupReq               (First)
pcieport 0000:00:03.0: AER:   TLP Header: 0x34008000 0x02000010 0x00000000 0x80008000
```

可以运行该行代码解决：

```
# add this in crontab @reboot
setpci -s 00:03.0 ECAP_AER+0x08.L=0x00100000:0x0010000
```
