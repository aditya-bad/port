// Strategy side, Node.
const net = require('net');
const ema = new Float64Array(4096);
let buf = Buffer.alloc(0);
const c = net.createConnection(process.argv[2]);
c.setNoDelay && c.setNoDelay(true);
c.on('data', d => {
  buf = buf.length ? Buffer.concat([buf, d]) : d;
  while (buf.length >= 4) {
    const n = buf.readUInt32LE(0);
    if (buf.length < 4 + n) break;
    const cnt = buf.readUInt16LE(5);
    let out = 0;
    for (let i = 0; i < cnt; i++) {
      const o = 4 + 3 + 32 * i;
      const id = buf.readUInt32LE(o), px = buf.readDoubleLE(o + 8);
      let e = ema[id & 4095]; e += 0.1 * (px - e); ema[id & 4095] = e;
      if (px > e * 1.5) out++;
    }
    buf = buf.subarray(4 + n);
    const r = Buffer.alloc(6); r.writeUInt32LE(2, 0); r.writeUInt16LE(out, 4); c.write(r);
  }
});
