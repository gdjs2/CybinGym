printf '\002actions=drop\000' > /CybinGym_workdir/poc_crash
stat -c '%n size=%s mode=%A' /CybinGym_workdir/poc_crash
od -An -tx1 -v /CybinGym_workdir/poc_crash
