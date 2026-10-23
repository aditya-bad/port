// The "supervisor": for each strategy process, send tick batches over a
// Unix socket and time the round trip (send batch -> strategy decodes,
// updates an EMA per instrument, replies with its commands).
use std::io::{Read, Write};
use std::os::unix::net::UnixListener;
use std::process::Command;
use std::time::Instant;

fn frame(n: usize, seq: u32) -> Vec<u8> {
    let mut v = Vec::with_capacity(4 + 3 + 32 * n);
    v.extend_from_slice(&((3 + 32 * n) as u32).to_le_bytes());
    v.push(1);
    v.extend_from_slice(&(n as u16).to_le_bytes());
    for i in 0..n {
        v.extend_from_slice(&(i as u32 * 7).to_le_bytes());
        v.extend_from_slice(&seq.to_le_bytes());
        v.extend_from_slice(&(25000.0 + (seq % 100) as f64 * 0.05 + i as f64).to_le_bytes());
        v.extend_from_slice(&(1_700_000_000_000i64 + seq as i64).to_le_bytes());
        v.extend_from_slice(&75u32.to_le_bytes());
        v.extend_from_slice(&[0u8; 4]);
    }
    v
}

fn pct(xs: &mut Vec<f64>, p: f64) -> f64 { xs.sort_by(|a, b| a.partial_cmp(b).unwrap()); xs[((xs.len() as f64 * p) as usize).min(xs.len() - 1)] }

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let (name, cmd) = (&args[1], &args[2..]);
    for &n in &[1usize, 16, 256] {
        let path = format!("/tmp/linck-ipc-{}-{}.sock", name, n);
        let _ = std::fs::remove_file(&path);
        let l = UnixListener::bind(&path).unwrap();
        let mut child = Command::new(&cmd[0]).args(&cmd[1..]).arg(&path).spawn().unwrap();
        let (mut s, _) = l.accept().unwrap();
        let mut rep = [0u8; 6];
        let iters = if n == 256 { 5000 } else { 20000 };
        let mut lat = Vec::with_capacity(iters);
        let frames: Vec<Vec<u8>> = (0..64).map(|k| frame(n, k as u32)).collect();
        for i in 0..(iters + 2000) {
            let f = &frames[i % 64];
            let t = Instant::now();
            s.write_all(f).unwrap();
            s.read_exact(&mut rep).unwrap();
            if i >= 2000 { lat.push(t.elapsed().as_secs_f64() * 1e6); }
        }
        let mean = lat.iter().sum::<f64>() / lat.len() as f64;
        let (p50, p99, p999) = (pct(&mut lat, 0.5), pct(&mut lat, 0.99), pct(&mut lat, 0.999));
        println!("{name:7} batch={n:3}  p50={p50:7.1}us  p99={p99:7.1}us  p99.9={p999:7.1}us  mean={mean:7.1}us  per-tick={:.2}us  ({} round trips)", mean / n as f64, iters);
        drop(s);
        let _ = child.wait();
        let _ = std::fs::remove_file(&path);
    }
}
