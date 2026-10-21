// SPDX-License-Identifier: MIT
use flattrace::Writer;
use serde_json::json;
use std::{env, fs, hint::black_box, path::Path, time::Instant};

fn size(directory: &Path) -> u64 {
    fs::read_dir(directory).unwrap().map(|entry| entry.unwrap().metadata().unwrap().len()).sum()
}
fn main() {
    let args: Vec<_> = env::args().collect();
    let iterations: u64 = args[1].parse().unwrap();
    let mode = &args[2];
    let directory = Path::new(&args[3]);
    let activation_iterations: u64 = args[4].parse().unwrap();
    env::remove_var("FLATTRACE_TRACEPARENT");
    env::set_var("FLATTRACE_DIR", if mode == "off" { "" } else { &args[3] });
    env::set_var("FLATTRACE_LEVEL", if mode == "off" { "1" } else { mode });
    let mut writer = Writer::from_env("writer-bench", "synthetic", vec![], "synthetic", json!({})).unwrap();
    let payload = json!({"count":1, "bytes":128, "label":"synthetic", "values":[1,2,3], "unknown":null});
    let before = size(directory);
    let started = Instant::now();
    for _ in 0..iterations {
        // black_box prevents the disabled cached branch being optimized away.
        if let Some(w) = black_box(&mut writer).as_mut() {
            let span = w.start("operation", payload.clone(), None).unwrap();
            w.point("summary", payload.clone(), Some(&span), None).unwrap();
            if w.level() >= 2 { w.point("debug", payload.clone(), Some(&span), None).unwrap(); }
            w.end(&span, "ok", payload.clone(), None).unwrap();
        }
    }
    let elapsed = started.elapsed().as_secs_f64();
    let bytes = size(directory) - before;
    let mut activation = None;
    if let Some(w) = writer { w.finish("ok").unwrap(); }
    else {
        let started = Instant::now();
        for _ in 0..activation_iterations {
            assert!(Writer::from_env("writer-bench", "synthetic", vec![], "synthetic", json!({})).unwrap().is_none());
        }
        activation = Some(started.elapsed().as_secs_f64() * 1e9 / activation_iterations as f64);
    }
    let status = fs::read_to_string("/proc/self/status").unwrap();
    let peak_rss: u64 = status.lines().find(|line| line.starts_with("VmHWM:"))
        .unwrap().split_whitespace().nth(1).unwrap().parse().unwrap();
    println!("{}", json!({"elapsed_s":elapsed, "event_bytes":bytes,
        "activation_ns_per_call":activation, "peak_rss_KiB":peak_rss}));
}
