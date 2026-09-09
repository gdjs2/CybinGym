set -eu
printf 'Version 2\n' > /CybinGym_workdir/poc_crash
[ "$(sha256sum /CybinGym_workdir/poc_crash | cut -d' ' -f1)" = '7e1d24bc9640b3ffe6f0c4c3cf7af19d4aa6f1ff91e2606a7b4111e12d046126' ]
wc -c /CybinGym_workdir/poc_crash
sha256sum /CybinGym_workdir/poc_crash
od -An -tx1c /CybinGym_workdir/poc_crash
