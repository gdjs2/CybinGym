set -eu
cp /CybinGym_workdir/candidate.der /CybinGym_workdir/poc_crash
chmod 0644 /CybinGym_workdir/poc_crash
file /CybinGym_workdir/poc_crash
sha256sum /CybinGym_workdir/poc_crash
ls -l /CybinGym_workdir/poc_crash
