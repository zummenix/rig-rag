use std::fs;

/// Best-effort memory report for the ingest step: process peak RSS and, when
/// running in a Linux container, the cgroup v2 memory peak. Both are silent
/// no-ops on platforms that do not expose the files.
pub fn report_memory() {
    if let Some(bytes) = peak_rss_bytes() {
        println!("Peak RSS: {}", human(bytes));
    }
    if let Some(bytes) = cgroup_peak_bytes() {
        println!("Container peak memory: {}", human(bytes));
    }
}

fn peak_rss_bytes() -> Option<u64> {
    let status = fs::read_to_string("/proc/self/status").ok()?;
    let line = status.lines().find(|line| line.starts_with("VmHWM:"))?;
    let kib: u64 = line.split_whitespace().nth(1)?.parse().ok()?;
    Some(kib * 1024)
}

fn cgroup_peak_bytes() -> Option<u64> {
    fs::read_to_string("/sys/fs/cgroup/memory.peak")
        .ok()?
        .trim()
        .parse()
        .ok()
}

fn human(bytes: u64) -> String {
    const MIB: f64 = 1024.0 * 1024.0;
    const GIB: f64 = MIB * 1024.0;
    let bytes_f = bytes as f64;
    if bytes_f >= GIB {
        format!("{:.2} GiB", bytes_f / GIB)
    } else if bytes_f >= MIB {
        format!("{:.1} MiB", bytes_f / MIB)
    } else {
        format!("{bytes} bytes")
    }
}
