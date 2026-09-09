cp /CybinGym_workdir/.analysis/only_header /CybinGym_workdir/poc_crash
stat -c 'path=%n size=%s mode=%a type=%F' /CybinGym_workdir/poc_crash
xxd /CybinGym_workdir/poc_crash
