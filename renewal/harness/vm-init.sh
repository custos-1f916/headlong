#!/bin/sh
# PID 1 inside the disposable VM, never executed in the production LXC.
set -eu
mountpoint -q /proc || mount -t proc proc /proc
mountpoint -q /sys || mount -t sysfs sysfs /sys
mkdir -p /dev/shm
mount -t tmpfs -o size=64m,nosuid,nodev tmpfs /dev/shm
mount -t tmpfs -o size=64m,nosuid,nodev tmpfs /run
mount -t tmpfs -o size=256m,nosuid,nodev tmpfs /tmp
mount -t tmpfs -o size=1100m,nosuid,nodev tmpfs /work
mount -t tmpfs -o size=128m,nosuid,nodev tmpfs /home
mkdir -p /home/test /run/input
mount -o ro,nosuid,nodev /dev/vdb /run/input
# These links were created when the immutable test image was built.
for n in /sys/class/net/*; do
 iface=${n##*/}
 [ "$iface" = lo ] && continue
 ip link set "$iface" up
 ip addr add 10.0.2.15/24 dev "$iface"
done
ip link set lo up
ulimit -u 256 2>/dev/null || true
/usr/bin/env -i HOME=/home/test PATH=/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 /usr/bin/python3 /run/input/entry.py || true
sync
poweroff -f
