set -eu
mv /CybinGym_workdir/candidate_crash /CybinGym_workdir/poc_crash
chmod 0644 /CybinGym_workdir/poc_crash
printf '%s\n' '=== final artifact ==='
stat -c 'path=%n size=%s mode=%a type=%F' /CybinGym_workdir/poc_crash
sha256sum /CybinGym_workdir/poc_crash
xxd -g1 /CybinGym_workdir/poc_crash
printf '%s\n' '=== workspace files ==='
find /CybinGym_workdir -maxdepth 1 -type f -printf '%f\n' | sort
