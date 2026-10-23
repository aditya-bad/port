# Strategy side, Python: read framed tick batches, update an EMA per instrument, reply.
import socket, struct, sys
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.connect(sys.argv[1])
f = s.makefile("rb", buffering=65536)
ema = [0.0] * 4096
REC = struct.Struct("<IIdqI4x")  # id, seq, price, ts, vol (32 B)
while True:
    h = f.read(4)
    if len(h) < 4: break
    n = struct.unpack("<I", h)[0]
    body = f.read(n)
    cnt = struct.unpack_from("<H", body, 1)[0]
    out = 0
    for i in range(cnt):
        iid, seq, px, ts, vol = REC.unpack_from(body, 3 + 32 * i)
        e = ema[iid & 4095]; e += 0.1 * (px - e); ema[iid & 4095] = e
        if px > e * 1.5: out += 1
    s.sendall(struct.pack("<IH", 2, out))
