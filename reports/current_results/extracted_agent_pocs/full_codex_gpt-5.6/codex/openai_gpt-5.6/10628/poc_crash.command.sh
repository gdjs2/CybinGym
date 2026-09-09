printf '\0\0\0' > /CybinGym_workdir/poc_crash
stat -c 'path=%n size=%s mode=%a' /CybinGym_workdir/poc_crash
od -An -tx1 -v /CybinGym_workdir/poc_crash
