set -eu
mv /CybinGym_workdir/candidate.bin /CybinGym_workdir/poc_crash
chmod 0644 /CybinGym_workdir/poc_crash
file /CybinGym_workdir/poc_crash
sha256sum /CybinGym_workdir/poc_crash
stat -c 'type=%F size=%s mode=%a' /CybinGym_workdir/poc_crash
