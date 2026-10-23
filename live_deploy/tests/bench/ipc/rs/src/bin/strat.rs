// Strategy side, Rust.
use std::io::{BufReader, Read, Write};
use std::os::unix::net::UnixStream;
fn main() {
    let p = std::env::args().nth(1).unwrap();
    let mut w = UnixStream::connect(&p).unwrap();
    let mut r = BufReader::with_capacity(65536, w.try_clone().unwrap());
    let mut ema = vec![0f64; 4096];
    let mut h = [0u8; 4];
    let mut body = vec![0u8; 1 << 20];
    loop {
        if r.read_exact(&mut h).is_err() { return; }
        let n = u32::from_le_bytes(h) as usize;
        r.read_exact(&mut body[..n]).unwrap();
        let b = &body[..n];
        let cnt = u16::from_le_bytes([b[1], b[2]]) as usize;
        let mut k = 0u16;
        for i in 0..cnt {
            let o = 3 + 32 * i;
            let id = u32::from_le_bytes(b[o..o + 4].try_into().unwrap()) as usize & 4095;
            let px = f64::from_le_bytes(b[o + 8..o + 16].try_into().unwrap());
            let e = ema[id] + 0.1 * (px - ema[id]);
            ema[id] = e;
            if px > e * 1.5 { k += 1; }
        }
        let mut out = [0u8; 6];
        out[..4].copy_from_slice(&2u32.to_le_bytes());
        out[4..].copy_from_slice(&k.to_le_bytes());
        w.write_all(&out).unwrap();
    }
}
