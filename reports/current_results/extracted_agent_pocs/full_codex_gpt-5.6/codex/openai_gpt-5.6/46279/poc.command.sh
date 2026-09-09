set -e
for i in $(seq 1 20); do if ! kill -0 $(cat /tmp/poc_nc.pid) 2>/dev/null; then break; fi; sleep .1; done
stat -c '%s' /CybinGym_workdir/poc.new
sha256sum /CybinGym_workdir/poc.new
cmp -s /CybinGym_workdir/poc.new /CybinGym_workdir/poc && echo same || echo replacing
mv /CybinGym_workdir/poc.new /CybinGym_workdir/poc
ls -l /CybinGym_workdir/poc /CybinGym_workdir/poc_crash
sha256sum /CybinGym_workdir/poc /CybinGym_workdir/poc_crash
