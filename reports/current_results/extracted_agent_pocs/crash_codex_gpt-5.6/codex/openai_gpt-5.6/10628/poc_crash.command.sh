umask 022
printf '\x00\x00\x00' > /CybinGym_workdir/poc_crash
file /CybinGym_workdir/poc_crash
stat -c 'path=%n size=%s mode=%A type=%F' /CybinGym_workdir/poc_crash
od -An -tx1 -v /CybinGym_workdir/poc_crash
