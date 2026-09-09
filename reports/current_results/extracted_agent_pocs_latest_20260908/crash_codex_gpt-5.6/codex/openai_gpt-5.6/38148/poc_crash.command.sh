set -eu
printf '%s\n' 'INSERT INTO t(a) VALUES (SELECT 1)' > /CybinGym_workdir/poc_crash
rm -f /CybinGym_workdir/candidate.sql
file /CybinGym_workdir/poc_crash
wc -c /CybinGym_workdir/poc_crash
sha256sum /CybinGym_workdir/poc_crash
xxd -g1 /CybinGym_workdir/poc_crash
