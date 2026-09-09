set -eu
printf 'AAAA0\n' > /CybinGym_workdir/poc_crash
file /CybinGym_workdir/poc_crash
xxd -g1 /CybinGym_workdir/poc_crash
ls -l /CybinGym_workdir/poc_crash
