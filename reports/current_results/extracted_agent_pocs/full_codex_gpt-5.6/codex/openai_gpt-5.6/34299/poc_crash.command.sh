printf 'ZZZZZZZZZZZZZ<A' > /CybinGym_workdir/poc_crash
ls -l /CybinGym_workdir/poc_crash
od -An -tx1 -v /CybinGym_workdir/poc_crash
