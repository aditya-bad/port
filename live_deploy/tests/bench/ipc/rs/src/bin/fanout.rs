// One supervisor, K strategy processes: each round sends every process its
// batch (16 ticks), then waits for every reply. Measures how long a round
// takes on this host as K grows: how many strategies one host can serve
// at a given tick cadence.
use std::io::{Read, Write};
use std::os::unix::net::{UnixListener, UnixStream};
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

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let k: usize = args[1].parse().unwrap();
    let rounds: usize = args[2].parse().unwrap();
    let cmd = &args[3..];
    let path = format!("/tmp/linck-fan-{}.sock", k);
    let _ = std::fs::remove_file(&path);
    let l = UnixListener::bind(&path).unwrap();
    let mut kids = vec![];
    let mut socks: Vec<UnixStream> = vec![];
    for _ in 0..k {
        kids.push(Command::new(&cmd[0]).args(&cmd[1..]).arg(&path).spawn().unwrap());
        socks.push(l.accept().unwrap().0);
    }
    let frames: Vec<Vec<u8>> = (0..16).map(|q| frame(16, q)).collect();
    let mut rep = [0u8; 6];
    let mut lat = vec![];
    for r in 0..(rounds + 50) {
        let t = Instant::now();
        for s in socks.iter_mut() { s.write_all(&frames[r % 16]).unwrap(); }
        for s in socks.iter_mut() { s.read_exact(&mut rep).unwrap(); }
        if r >= 50 { lat.push(t.elapsed().as_secs_f64() * 1e3); }
    }
    lat.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let p = |q: f64| lat[((lat.len() as f64 * q) as usize).min(lat.len() - 1)];
    println!("{:>4} processes ({}): round p50={:.2}ms p99={:.2}ms  -> {:.0} strategy-batches/s", k, cmd.join(" "), p(0.5), p(0.99), k as f64 / (p(0.5) / 1e3));
    drop(socks);
    for mut c in kids { let _ = c.wait(); }
    let _ = std::fs::remove_file(&path);
}
