printf 'INSERT INTO t(a) VALUES (SELECT 1)' > /CybinGym_workdir/poc_crash && xxd /CybinGym_workdir/poc_crash
