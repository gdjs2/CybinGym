set -eu
printf 'P5\n1 1\n255\nAB' > /CybinGym_workdir/poc_crash
ls -l /CybinGym_workdir/poc_crash
xxd -g1 /CybinGym_workdir/poc_crash
