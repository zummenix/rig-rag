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

/// Peak resident set size of this process in bytes, via `getrusage`. Returns
/// `None` on platforms without `getrusage` or when the call fails. Used both by
/// the CLI summary and the `ingest --report` memory block.
#[cfg(unix)]
pub fn peak_rss_bytes() -> Option<u64> {
    // `ru_maxrss` is a `c_long`; platforms disagree on its unit.
    unsafe {
        let mut usage: libc::rusage = std::mem::zeroed();
        if libc::getrusage(libc::RUSAGE_SELF, &mut usage) != 0 {
            return None;
        }
        let max_rss = u64::try_from(usage.ru_maxrss).ok()?;
        // macOS reports bytes, Linux (and the other unixes we build for) report
        // kibibytes.
        #[cfg(target_os = "macos")]
        {
            Some(max_rss)
        }
        #[cfg(not(target_os = "macos"))]
        {
            Some(max_rss * 1024)
        }
    }
}

#[cfg(not(unix))]
pub fn peak_rss_bytes() -> Option<u64> {
    None
}

/// cgroup v2 peak memory in bytes; `None` outside a Linux container with the
/// memory controller exposed.
pub fn cgroup_peak_bytes() -> Option<u64> {
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
