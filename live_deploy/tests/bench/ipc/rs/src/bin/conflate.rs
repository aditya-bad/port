// The conflation core: a latest-value slot per instrument; each subscriber
// keeps a "changed since my last batch" set; a flush builds each
// subscriber's batch from the slots (latest only). Measures updates/s on
// one core and the cost of a flush, for the sizes in the doc.
use std::time::Instant;

#[derive(Clone, Copy, Default)]
#[repr(C)]
struct Rec { id: u32, seq: u32, px: i64, ts: i64, vol: u64, oi: u64, bid: i64, ask: i64, bq: u32, aq: u32, pad: [u8; 24] } // 96 B

struct Sub { dirty: Vec<u64>, list: Vec<u32>, out: Vec<Rec> }

fn main() {
    let n_inst = 5000usize;
    for &(subs, per) in &[(64usize, 500usize), (256, 500), (64, 5000)] {
        let mut slots = vec![Rec::default(); n_inst];
        let mut who: Vec<Vec<u32>> = vec![vec![]; n_inst];
        let mut s: Vec<Sub> = (0..subs).map(|_| Sub { dirty: vec![0; (n_inst + 63) / 64], list: vec![], out: Vec::with_capacity(per) }).collect();
        let mut x = 88172645463325252u64;
        let mut rnd = || { x ^= x << 13; x ^= x >> 7; x ^= x << 17; x };
        for (k, sub) in s.iter().enumerate() { let _ = sub; for _ in 0..per { let i = (rnd() % n_inst as u64) as usize; if !who[i].contains(&(k as u32)) { who[i].push(k as u32); } } }
        let updates = 5_000_000usize;
        let ids: Vec<u32> = (0..updates).map(|_| (rnd() % n_inst as u64) as u32).collect();
        let t = Instant::now();
        let mut flushes = 0; let mut sent = 0usize; let mut flush_time = 0f64;
        for (u, &i) in ids.iter().enumerate() {
            let r = &mut slots[i as usize];
            r.id = i; r.seq += 1; r.px = 2_500_000 + (u as i64 & 1023);
            for &k in &who[i as usize] {
                let sub = &mut s[k as usize];
                let (w, b) = (i as usize / 64, 1u64 << (i % 64));
                if sub.dirty[w] & b == 0 { sub.dirty[w] |= b; sub.list.push(i); }
            }
            if u % 20_000 == 19_999 {   // a flush every 20k updates (~every 10 ms at 2M/s)
                let f = Instant::now();
                for sub in s.iter_mut() {
                    sub.out.clear();
                    for &i in &sub.list { sub.out.push(slots[i as usize]); sub.dirty[i as usize / 64] = 0; }
                    sent += sub.out.len(); sub.list.clear();
                }
                flush_time += f.elapsed().as_secs_f64(); flushes += 1;
            }
        }
        let dt = t.elapsed().as_secs_f64();
        println!("{subs:4} subscribers x {per:4} instruments each: {:.1} M updates/s on one core (incl. flushes); a flush every 20k updates sends {:.0} records, takes {:.0} us", updates as f64 / dt / 1e6, sent as f64 / flushes as f64, flush_time / flushes as f64 * 1e6);
    }
}
