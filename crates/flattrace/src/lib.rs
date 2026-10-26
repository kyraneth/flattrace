// SPDX-License-Identifier: MIT
//! Synchronous Linux writer: std + serde_json; each process owns its file.
use serde_json::{json, Value};
use std::{collections::BTreeMap, fs::{File, OpenOptions}, io::{self, Read, Write},
          path::Path, time::{Instant, SystemTime, UNIX_EPOCH}};

pub const CONTEXT_ENV: &str = "FLATTRACE_TRACEPARENT";
pub const LEVEL_ENV: &str = "FLATTRACE_LEVEL";
fn level_from_env() -> (u64, Option<String>) {
    let raw = std::env::var_os(LEVEL_ENV).unwrap_or_default();
    let raw = raw.to_string_lossy();
    // Unicode White_Space plus information separators: Python's str.strip set.
    let raw = raw.trim_matches(|c: char| c.is_whitespace() || matches!(c, '\u{1c}'..='\u{1f}'));
    if raw.is_empty() { return (1, None); }
    if raw.bytes().all(|b| b.is_ascii_digit()) {
        // u64 is the integer representation used by this writer and serde_json.
        if let Ok(level) = raw.parse::<u64>() { return (level, None); }
    }
    (1, Some(format!("invalid {LEVEL_ENV} '{raw}' treated as 1")))
}
static WARNED: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);
fn warn(error: &io::Error) { warn_text(&format!("tracing disabled: {error}")); }
fn warn_text(message: &str) {
    let pid = std::process::id();
    if WARNED.swap(pid, std::sync::atomic::Ordering::Relaxed) != pid {
        let _ = writeln!(io::stderr(), "flattrace: {message}");
    }
}
fn invalid(message: &str) -> io::Error { io::Error::new(io::ErrorKind::InvalidInput, message) }
fn id(bytes: usize) -> io::Result<String> {
    read_id(&mut File::open("/dev/urandom")?, bytes)
}
fn read_id(source: &mut impl Read, bytes: usize) -> io::Result<String> {
    // W3C trace/span ID widths; OS entropy on Linux, never a userspace PRNG.
    const HEX: &[u8] = b"0123456789abcdef";
    let mut raw = vec![0u8; bytes];
    loop {
        source.read_exact(&mut raw)?;
        if raw.iter().any(|v| *v != 0) {
            let mut value = String::with_capacity(bytes * 2);
            for byte in raw {
                value.push(HEX[(byte >> 4) as usize] as char);
                value.push(HEX[(byte & 15) as usize] as char);
            }
            return Ok(value);
        }
    }
}
fn hex(s: &str, width: usize, nonzero: bool) -> bool {
    s.len() == width && s.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
        && (!nonzero || s.bytes().any(|c| c != b'0'))
}
fn context(value: &str) -> io::Result<(String, Option<String>, String)> {
    let p: Vec<_> = value.split('-').collect();
    if p.len() != 4 || p[0] != "00" || !hex(p[1], 32, true)
        || !hex(p[2], 16, true) || !hex(p[3], 2, false) {
        return Err(invalid("invalid FLATTRACE_TRACEPARENT"));
    }
    Ok((p[1].into(), Some(p[2].into()), p[3].into()))
}
fn wall() -> io::Result<String> {
    let now = SystemTime::now().duration_since(UNIX_EPOCH).map_err(|_| invalid("clock before Unix epoch"))?;
    // Gregorian UTC calendar constants, not workload limits.
    let seconds = now.as_secs();
    let mut days = seconds / 86400;
    let mut year = 1970u64;
    let leap = |y: u64| y % 4 == 0 && (y % 100 != 0 || y % 400 == 0);
    loop {
        let n = if leap(year) { 366 } else { 365 };
        if days < n { break; }
        days -= n;
        year += 1;
    }
    let months = [31, if leap(year) { 29 } else { 28 }, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    let mut month = 0;
    while days >= months[month] { days -= months[month]; month += 1; }
    Ok(format!("{year:04}-{:02}-{:02}T{:02}:{:02}:{:02}.{:09}Z", month + 1, days + 1,
               seconds % 86400 / 3600, seconds % 3600 / 60, seconds % 60, now.subsec_nanos()))
}

fn add_note(data: &mut Value, note: &str) {
    if data.get("notes").is_none() { data["notes"] = json!([]); }
    if !data["notes"].is_array() {
        let previous = data["notes"].take();
        data["notes"] = json!([previous]);
    }
    data["notes"].as_array_mut().unwrap().push(json!(note));
}
/// Capture floats before serde_json::Value erases non-finite values into null.
pub fn set_number(data: &mut Value, key: &str, value: f64) -> io::Result<()> {
    if !data.is_object() { return Err(invalid("data must be an object")); }
    if !value.is_finite() {
        add_note(data, "non-finite number replaced with null");
    }
    data[key] = json!(value);
    Ok(())
}
// The same masking set as Python; regexes compile only once, on first argv use.
static CREDENTIALS: std::sync::LazyLock<[regex::Regex; 5]> = std::sync::LazyLock::new(|| {
    // Python re.I also folds dotted/dotless i. Python's word set is L/N/underscore;
    // its whitespace set adds the four information separators to Unicode White_Space.
    let name = r"(?:token|password|passwd|secret|ap[iİı][-_]?key|access[-_]?key|key)";
    let space = r"\s\x1c-\x1f";
    let boundary = r"(^|[^\p{L}\p{N}_])";
    [
        format!(r"(?i)^-*{name}$"),
        format!(r"(?i){boundary}(Author[iİı]zat[iİı]on[{space}]*:[{space}]*)(?:Bearer|Bas[iİı]c)[{space}]+[^{space},;]+"),
        format!(r"(?i)(https?://)[^/{space}:@]+:[^/{space}@]+@"),
        format!(r#"(?i){boundary}({name}[{space}]*[=:][{space}]*)(?:"[^"]*"|'[^']*'|[^{space},;&]+)"#),
        format!(r#"(?i)(-+{name}[{space}]+)(?:"[^"]*"|'[^']*'|[^{space},;]+)"#),
    ].map(|pattern| regex::Regex::new(&pattern).expect("static credential pattern"))
});
pub fn sanitize_argv(argv: Vec<String>) -> Vec<String> {
    let mut hide_next = false;
    argv.into_iter().map(|value| {
        if hide_next { hide_next = false; return "<redacted>".into(); }
        let rules = &*CREDENTIALS;
        if let Some((name, _)) = value.split_once('=') {
            if rules[0].is_match(name) { return format!("{name}=<redacted>"); }
        }
        hide_next = rules[0].is_match(&value);
        let value = rules[1].replace_all(&value, "${1}${2}<redacted>");
        let value = rules[2].replace_all(&value, "${1}<redacted>@");
        let value = rules[3].replace_all(&value, "${1}${2}<redacted>");
        rules[4].replace_all(&value, "${1}<redacted>").into_owned()
    }).collect()
}
fn object_data(data: Value) -> Value {
    if data.is_object() { data }
    else { json!({"value":data, "notes":["non-object data wrapped in value"]}) }
}

pub struct Writer {
    file: Option<File>, best_effort: bool, tool: String, version: String, run: String, trace: String,
    root: String, flags: String, pid: u32, seq: u64, level: u64,
    random: File,
    active: BTreeMap<String, (String, Option<String>, Instant)>,
}
impl Writer {
    pub fn create(path: &Path, tool: &str, version: &str, argv: Vec<String>, host: &str) -> io::Result<Self> {
        Self::create_with_data(path, tool, version, argv, host, json!({}))
    }
    pub fn create_with_data(path: &Path, tool: &str, version: &str, argv: Vec<String>, host: &str, data: Value) -> io::Result<Self> {
        Self::create_at(path, tool, version, argv, host, data, id(16)?, false)
    }
    /// Products use this entry point: off without FLATTRACE_DIR or at level 0;
    /// sink failures never fail the product.
    pub fn from_env(tool: &str, version: &str, argv: Vec<String>, host: &str, mut data: Value) -> io::Result<Option<Self>> {
        let (level, note) = level_from_env();
        if level == 0 { return Ok(None); }
        let directory = match std::env::var_os("FLATTRACE_DIR") {
            Some(v) if !v.is_empty() => std::path::PathBuf::from(v),
            _ => return Ok(None),
        };
        data = object_data(data);
        data["level"] = json!(level);
        if let Some(note) = note { add_note(&mut data, &note); }
        if tool.is_empty() || !tool.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_' || b == b'-') {
            return Err(invalid("environment writer tool must be a lowercase slug"));
        }
        if let Err(e) = std::fs::create_dir_all(&directory) { warn(&e); return Ok(None); }
        loop {
            let run = id(16)?;
            let mut stamp = wall()?.replace(['-', ':'], "");
            // Shared filename precision is milliseconds, the Node Date resolution.
            stamp.truncate(stamp.len() - 7); // Remove six sub-ms digits and Z.
            stamp.push('Z');
            let path = directory.join(format!("{stamp}-{tool}-{run}.jsonl"));
            match Self::create_at(&path, tool, version, argv.clone(), host, data.clone(), run, true) {
                Ok(mut w) => { w.level = level; return Ok(Some(w)); },
                Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(e) if e.kind() != io::ErrorKind::InvalidInput => { warn(&e); return Ok(None); },
                Err(e) => return Err(e),
            }
        }
    }
    fn create_at(path: &Path, tool: &str, version: &str, argv: Vec<String>, host: &str,
                 mut data: Value, run: String, best_effort: bool) -> io::Result<Self> {
        data = object_data(data);
        let (trace, parent, flags) = match std::env::var(CONTEXT_ENV) {
            Ok(v) => context(&v)?,
            Err(std::env::VarError::NotPresent) => (id(16)?, None, "00".into()),
            Err(_) => return Err(invalid("invalid FLATTRACE_TRACEPARENT")),
        };
        let mut w = Self {
            file: Some(OpenOptions::new().create_new(true).append(true).open(path)?), best_effort,
            tool: tool.into(), version: version.into(), run, trace,
            root: id(8)?, flags, pid: std::process::id(), seq: 0, level: 1, active: BTreeMap::new(),
            random: File::open("/dev/urandom")?,
        };
        let root = w.root.clone();
        data["argv"] = json!(sanitize_argv(argv));
        data["pid"] = json!(w.pid);
        data["host"] = json!(host);
        data["version"] = json!(version);
        w.begin("run", data, parent, root)?;
        Ok(w)
    }
    fn emit(&mut self, event: &str, state: &str, span: &str, parent: Option<&str>,
            data: Value, end: Option<(&str, f64)>, error: Option<Value>) -> io::Result<()> {
        if std::process::id() != self.pid {
            warn(&invalid("writer inherited across fork; create a new writer"));
            self.file = None;
            return Ok(());
        }
        let data = object_data(data);
        if let Some(e) = &error {
            if !e.is_object() || !e["kind"].is_string() || !e["message"].is_string() {
                return Err(invalid("error requires string kind and message"));
            }
        }
        let mut row = json!({"v":1, "seq":self.seq, "tool":self.tool, "tool_version":self.version,
            "run":self.run, "trace":self.trace, "span":span, "parent":parent,
            "event":event, "state":state, "t":wall()?, "data":data});
        if let Some((outcome, took)) = end {
            row["outcome"] = json!(outcome);
            row["took_ms"] = json!(took);
        }
        if let Some(error) = error { row["error"] = error; }
        let mut line = serde_json::to_vec(&row)?;
        line.push(b'\n');
        self.seq += 1;
        if let Some(file) = &mut self.file {
            if let Err(e) = file.write_all(&line).and_then(|_| file.flush()) {
                if !self.best_effort { return Err(e); }
                warn(&e);
                self.file = None;
            }
        }
        Ok(())
    }
    fn begin(&mut self, event: &str, data: Value, parent: Option<String>, span: String) -> io::Result<String> {
        self.emit(event, "start", &span, parent.as_deref(), data, None, None)?;
        self.active.insert(span.clone(), (event.into(), parent, Instant::now()));
        Ok(span)
    }
    pub fn level(&self) -> u64 { self.level }
    pub fn root_span(&self) -> &str { &self.root }
    pub fn trace_id(&self) -> &str { &self.trace }
    pub fn traceparent(&self, span: Option<&str>) -> io::Result<String> {
        let span = span.unwrap_or(&self.root);
        if !self.active.contains_key(span) { return Err(invalid("inactive span")); }
        Ok(format!("00-{}-{span}-{}", self.trace, self.flags))
    }
    pub fn start(&mut self, event: &str, data: Value, parent: Option<&str>) -> io::Result<String> {
        if event == "run" { return Err(invalid("run is reserved")); }
        let parent = parent.unwrap_or(&self.root).to_owned();
        if !self.active.contains_key(&parent) { return Err(invalid("inactive parent")); }
        let span = read_id(&mut self.random, 8)?;
        self.begin(event, data, Some(parent), span)
    }
    pub fn point(&mut self, event: &str, mut data: Value, span: Option<&str>, error: Option<Value>) -> io::Result<()> {
        if event == "run" { return Err(invalid("run is reserved")); }
        let mut span = span.unwrap_or(&self.root).to_owned();
        if !self.active.contains_key(&span) {
            if !self.active.contains_key(&self.root) { return Ok(()); }
            data = object_data(data);
            data["late_span"] = json!(span);
            add_note(&mut data, "point after span end");
            span = self.root.clone();
        }
        let parent = self.active.get(&span).ok_or_else(|| invalid("inactive span"))?.1.clone();
        self.emit(event, "point", &span, parent.as_deref(), data, None, error)
    }
    pub fn end(&mut self, span: &str, outcome: &str, data: Value, error: Option<Value>) -> io::Result<()> {
        if span == self.root {
            let message = "run root can only end through finish()";
            if !self.best_effort { return Err(invalid(message)); }
            warn_text(message);
            return Ok(());
        }
        self.end_span(span, outcome, data, error)
    }
    fn end_span(&mut self, span: &str, outcome: &str, data: Value, error: Option<Value>) -> io::Result<()> {
        let (event, parent, begun) = self.active.get(span).ok_or_else(|| invalid("inactive span"))?.clone();
        self.emit(&event, "end", span, parent.as_deref(), data,
                  Some((outcome, begun.elapsed().as_secs_f64() * 1000.0)), error)?;
        self.active.remove(span);
        Ok(())
    }
    pub fn finish(self, outcome: &str) -> io::Result<()> { self.finish_with(outcome, json!({})) }
    /// End the run with a run-end payload (e.g. totals).
    pub fn finish_with(mut self, outcome: &str, data: Value) -> io::Result<()> {
        if self.active.keys().any(|span| span != &self.root) { return Err(invalid("local spans still active")); }
        self.end_span(&self.root.clone(), outcome, data, None)
    }
    // Dropping a writer closes the file without inventing an end.
}

#[cfg(test)]
mod tests {
    use super::*;
    fn target() -> std::path::PathBuf {
        let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../target");
        std::fs::create_dir_all(&dir).unwrap();
        dir.join(format!("rust-{}.jsonl", id(16).unwrap()))
    }
    #[test]
    fn shared_argv_vectors() {
        let vectors: Value = serde_json::from_str(include_str!("../../../tests/fixtures/sanitize-argv.json")).unwrap();
        for vector in vectors.as_array().unwrap() {
            let argv: Vec<String> = vector["argv"].as_array().unwrap().iter().map(|v| v.as_str().unwrap().into()).collect();
            assert_eq!(json!(sanitize_argv(argv.clone())), vector["expected"], "{}", vector["name"]);
            let path = target();
            Writer::create(&path, "synthetic", "1", argv, "synthetic").unwrap().finish("ok").unwrap();
            let rows: Vec<Value> = std::fs::read_to_string(&path).unwrap().lines().map(|s| serde_json::from_str(s).unwrap()).collect();
            assert_eq!(rows[0]["data"]["argv"], vector["expected"]);
            std::fs::remove_file(path).unwrap();
        }
    }
    #[test]
    fn only_finish_ends_root() {
        for harmless in [false, true] {
            let path = target();
            let mut w = Writer::create(&path, "synthetic", "1", vec![], "synthetic").unwrap();
            w.best_effort = harmless;
            let child = w.start("work", json!({}), None).unwrap();
            let root = w.root.clone();
            let result = w.end(&root, "ok", json!({}), None);
            if harmless { assert!(result.is_ok()); }
            else { assert!(result.unwrap_err().to_string().contains("root")); }
            assert!(w.active.contains_key(&root));
            assert_eq!(std::fs::read_to_string(&path).unwrap().lines().count(), 2);
            w.end(&child, "ok", json!({}), None).unwrap();
            w.finish("ok").unwrap();
            let checked = std::process::Command::new("python3").args(["-m", "flattrace", "check"]).arg(&path)
                .current_dir(Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")).output().unwrap();
            assert!(checked.status.success(), "{}", String::from_utf8_lossy(&checked.stdout));
            std::fs::remove_file(path).unwrap();
        }
    }
    #[test]
    fn nonobject_payload_degrades_and_writing_continues() {
        let path = target();
        let mut w = Writer::create_with_data(&path, "synthetic", "1", vec![], "synthetic", json!(["start"])).unwrap();
        w.point("payload", json!(["synthetic", 7]), None, None).unwrap();
        w.point("continued", json!({}), None, None).unwrap();
        w.finish("ok").unwrap();
        let rows: Vec<Value> = std::fs::read_to_string(&path).unwrap().lines().map(|s| serde_json::from_str(s).unwrap()).collect();
        assert_eq!(rows[0]["data"]["value"], json!(["start"]));
        assert_eq!(rows[1]["data"]["value"], json!(["synthetic", 7]));
        assert!(rows[1]["data"]["notes"].is_array());
        assert_eq!(rows[2]["event"], "continued");
        std::fs::remove_file(path).unwrap();
    }
    #[test]
    fn surrogate_values_cannot_enter_rust_strings() {
        // Rust strings contain Unicode scalar values; lone surrogates have no representation.
        assert!(char::from_u32(0xd800).is_none());
        assert!(serde_json::from_str::<Value>(r#"{"value":"\ud800"}"#).is_err());
        let path = target();
        let mut w = Writer::create(&path, "synthetic", "1", vec![], "synthetic").unwrap();
        w.point("unicode", json!({"value":String::from_utf16_lossy(&[0xd800])}), None, None).unwrap();
        w.finish("ok").unwrap();
        let rows: Vec<Value> = std::fs::read_to_string(&path).unwrap().lines().map(|s| serde_json::from_str(s).unwrap()).collect();
        assert_eq!(rows[1]["data"]["value"], "\u{FFFD}");
        std::fs::remove_file(path).unwrap();
    }
    #[test]
    fn entropy_ids_retry_zero_and_encode_lowercase_hex() {
        let raw = vec![0, 0, 0, 0, 0, 0, 0, 0, 0x0f, 0xab, 0xff, 0x01, 0x23, 0x45, 0x67, 0x89];
        let mut source = io::Cursor::new(raw);
        assert_eq!(read_id(&mut source, 8).unwrap(), "0fabff0123456789");
        assert_eq!(source.position(), 16);
        assert!(read_id(&mut source, 8).is_err());
    }
    #[test]
    fn write_read_and_flush() {
        let path = target();
        let mut w = Writer::create(&path, "test", "1", vec![], "test-host").unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap().lines().count(), 1);
        let span = w.start("read", json!({"path":"/tmp/input"}), None).unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap().lines().count(), 2);
        let p = context(&w.traceparent(Some(&span)).unwrap()).unwrap();
        assert_eq!(p.0, w.trace_id());
        assert_eq!(p.1.as_deref(), Some(span.as_str()));
        w.point("size", json!({"bytes":0}), Some(&span), None).unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap().lines().count(), 3);
        w.end(&span, "refused", json!({}), Some(json!({"kind":"input", "message":"not supported"}))).unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap().lines().count(), 4);
        w.finish("refused").unwrap();
        let rows: Vec<Value> = std::fs::read_to_string(&path).unwrap().lines()
            .map(|s| serde_json::from_str(s).unwrap()).collect();
        assert_eq!(rows.len(), 5);
        assert_eq!(rows[0]["event"], "run");
        assert_eq!(rows[3]["outcome"], "refused");
        assert!(rows[3]["took_ms"].is_number());
        assert_eq!(rows[4]["state"], "end");
        let checked = std::process::Command::new("python3")
            .args(["-m", "flattrace", "check"]).arg(&path)
            .current_dir(Path::new(env!("CARGO_MANIFEST_DIR")).join("../.."))
            .output().unwrap();
        assert!(checked.status.success(), "{}", String::from_utf8_lossy(&checked.stdout));
        std::fs::remove_file(path).unwrap();
    }
    #[test]
    fn capture_numbers_sanitized_argv_and_late_point() {
        let unfinished = target();
        let mut abandoned = Writer::create(&unfinished, "test", "1", vec![], "host").unwrap();
        abandoned.start("active", json!({}), None).unwrap();
        assert!(abandoned.finish("ok").is_err());
        assert_eq!(std::fs::read_to_string(&unfinished).unwrap().lines().count(), 2);
        std::fs::remove_file(unfinished).unwrap();
        let path = target();
        let argv = vec!["--token".into(), "fake-token".into(), "--password=fake-pass".into(), "key=fake-key".into(), "---token".into(), "fake-triple".into(), "--token=".into()];
        let mut w = Writer::create(&path, "test", "1", argv, "host").unwrap();
        let span = w.start("work", json!({}), None).unwrap();
        w.end(&span, "ok", json!({}), None).unwrap();
        w.point("late", json!({"notes":"caller note"}), Some(&span), None).unwrap();
        let mut data = json!({"unknown":null});
        set_number(&mut data, "finite", 0.0).unwrap();
        assert!(data.get("notes").is_none());
        set_number(&mut data, "nan", f64::NAN).unwrap();
        set_number(&mut data, "inf", f64::INFINITY).unwrap();
        assert!(data["nan"].is_null() && data["inf"].is_null());
        assert!(data["notes"].is_array());
        w.point("numbers", data, None, None).unwrap();
        w.finish("ok").unwrap();
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(!text.contains("fake-token") && !text.contains("fake-pass") && !text.contains("fake-key") && !text.contains("fake-triple"));
        let rows: Vec<Value> = text.lines().map(|s| serde_json::from_str(s).unwrap()).collect();
        for (seq, row) in rows.iter().enumerate() { assert_eq!(row["seq"], seq); }
        assert_eq!(rows[0]["data"]["pid"], std::process::id());
        assert_eq!(rows[3]["data"]["late_span"], span);
        assert_eq!(rows[3]["data"]["notes"], json!(["caller note", "point after span end"]));
        assert_eq!(rows[0]["data"]["argv"].as_array().unwrap().last().unwrap(), "--token=<redacted>");
        let checked = std::process::Command::new("python3").args(["-m", "flattrace", "check"]).arg(&path)
            .current_dir(Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")).output().unwrap();
        assert!(checked.status.success(), "{}", String::from_utf8_lossy(&checked.stdout));
        std::fs::remove_file(path).unwrap();
    }
    #[test]
    fn environment_entry_point() {
        let directory = target().with_extension("dir");
        let result = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "tests::environment_child", "--nocapture"])
            .env("FLATTRACE_TEST_DIRECTORY", &directory)
            .env(CONTEXT_ENV, "00-11111111111111111111111111111111-2222222222222222-01").output().unwrap();
        assert!(result.status.success(), "{} {}", String::from_utf8_lossy(&result.stdout), String::from_utf8_lossy(&result.stderr));
        assert_eq!(String::from_utf8_lossy(&result.stderr).matches("flattrace: tracing disabled").count(), 1);
        std::fs::remove_dir_all(directory).unwrap();
    }
    #[test]
    fn environment_child() {
        // Isolated subprocess so env changes cannot race the other tests.
        let directory = match std::env::var("FLATTRACE_TEST_DIRECTORY") { Ok(v) => v, Err(_) => return };
        std::env::remove_var("FLATTRACE_DIR");
        assert!(Writer::from_env("test", "1", vec![], "host", json!({})).unwrap().is_none());
        std::env::set_var("FLATTRACE_DIR", &directory);
        let mut w = Writer::from_env("test", "1", vec![], "host", json!({"task":"frames", "variant":"base"})).unwrap().unwrap();
        assert_eq!(w.trace, "11111111111111111111111111111111");
        assert!(w.traceparent(None).unwrap().ends_with("-01"));
        let run = w.run.clone();
        let path = std::fs::read_dir(&directory).unwrap().next().unwrap().unwrap().path();
        assert!(path.file_name().unwrap().to_str().unwrap().ends_with(&format!("-test-{run}.jsonl")));
        let span = w.start("launch", json!({}), None).unwrap();
        let child = w.start("operation", json!({}), Some(&span)).unwrap();
        w.end(&span, "ok", json!({}), None).unwrap();
        w.end(&child, "ok", json!({}), None).unwrap();
        w.finish("ok").unwrap();
        let rows: Vec<Value> = std::fs::read_to_string(&path).unwrap().lines().map(|s| serde_json::from_str(s).unwrap()).collect();
        assert_eq!(rows[0]["run"], run);
        assert_eq!(rows[0]["data"]["task"], "frames");
        let checked = std::process::Command::new("python3").args(["-m", "flattrace", "check"]).arg(&path)
            .current_dir(Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")).output().unwrap();
        assert!(checked.status.success(), "{}", String::from_utf8_lossy(&checked.stdout));
        let mut failing = Writer::from_env("test", "1", vec![], "host", json!({})).unwrap().unwrap();
        failing.file = Some(OpenOptions::new().write(true).open("/dev/full").unwrap());
        failing.point("operation", json!({}), None, None).unwrap();
        assert!(failing.file.is_none());
        failing.finish("ok").unwrap();
        let blocked = Path::new(&directory).join("blocked");
        std::fs::write(&blocked, "not a directory").unwrap();
        std::env::set_var("FLATTRACE_DIR", &blocked);
        assert!(Writer::from_env("test", "1", vec![], "host", json!({})).unwrap().is_none());
    }
    fn level_case(raw: Option<&str>, expected: u64, note: bool, inherit: bool) {
        let directory = target().with_extension("dir");
        let mut command = std::process::Command::new(std::env::current_exe().unwrap());
        command.args(["--exact", "tests::level_child", "--nocapture"])
            .env("FLATTRACE_TEST_LEVEL_DIRECTORY", &directory)
            .env("FLATTRACE_TEST_EXPECTED_LEVEL", expected.to_string())
            .env("FLATTRACE_TEST_LEVEL_NOTE", note.to_string())
            .env("FLATTRACE_TEST_LEVEL_INHERIT", inherit.to_string())
            .env_remove(CONTEXT_ENV).env_remove(LEVEL_ENV);
        if let Some(raw) = raw { command.env(LEVEL_ENV, raw); }
        let result = command.output().unwrap();
        assert!(result.status.success(), "{} {}", String::from_utf8_lossy(&result.stdout), String::from_utf8_lossy(&result.stderr));
        assert!(result.stderr.is_empty(), "{}", String::from_utf8_lossy(&result.stderr));
        if expected == 0 { assert!(!directory.exists()); }
        else { std::fs::remove_dir_all(directory).unwrap(); }
    }
    #[test]
    fn level_zero_writes_nothing() { level_case(Some("0"), 0, false, false); }
    #[test]
    fn default_level_is_one_and_recorded() {
        for raw in [None, Some(""), Some("  ")] { level_case(raw, 1, false, false); }
    }
    #[test]
    fn invalid_level_is_one_with_note() {
        // One beyond u64's representable range must not stop product work.
        for raw in ["loud", "-1", "+2", "1.5", "²", "٢", "２", "2²", "18446744073709551616"] {
            level_case(Some(raw), 1, true, false);
        }
    }
    #[test]
    fn level_whitespace_matches_python_unicode_set() {
        for raw in ["2\u{1c}", "\u{1c}2", "2\u{85}"] { level_case(Some(raw), 2, false, false); }
        level_case(Some("2\u{feff}"), 1, true, false);
    }
    #[test]
    fn numeric_level_is_recorded() {
        level_case(Some(" 002 "), 2, false, false);
        level_case(Some(&u64::MAX.to_string()), u64::MAX, false, false);
    }
    #[test]
    fn child_inherits_level() { level_case(Some("2"), 2, false, true); }
    #[test]
    fn level_child() {
        // Run only in an isolated subprocess: no process-global env races.
        let directory = match std::env::var("FLATTRACE_TEST_LEVEL_DIRECTORY") { Ok(v) => v, Err(_) => return };
        let expected: u64 = std::env::var("FLATTRACE_TEST_EXPECTED_LEVEL").unwrap().parse().unwrap();
        std::env::set_var("FLATTRACE_DIR", &directory);
        if expected == 0 {
            std::env::set_var(CONTEXT_ENV, "bad");
            assert!(Writer::from_env("test", "1", vec![], "host", json!({})).unwrap().is_none());
            assert!(!Path::new(&directory).exists());
            return;
        }
        let w = Writer::from_env("test", "1", vec![], "host", json!({"notes":"caller note", "level":0})).unwrap().unwrap();
        assert_eq!(w.level(), expected);
        let path = std::fs::read_dir(&directory).unwrap().map(|e| e.unwrap().path())
            .find(|p| p.file_name().unwrap().to_string_lossy().ends_with(&format!("-{}.jsonl", w.run))).unwrap();
        let rows: Vec<Value> = std::fs::read_to_string(&path).unwrap().lines().map(|s| serde_json::from_str(s).unwrap()).collect();
        assert_eq!(rows[0]["data"]["level"], expected);
        if std::env::var("FLATTRACE_TEST_LEVEL_NOTE").unwrap() == "true" {
            let raw = std::env::var(LEVEL_ENV).unwrap();
            assert_eq!(rows[0]["data"]["notes"], json!(["caller note", format!("invalid {LEVEL_ENV} '{}' treated as 1", raw.trim())]));
        } else { assert_eq!(rows[0]["data"]["notes"], "caller note"); }
        if std::env::var("FLATTRACE_TEST_LEVEL_INHERIT").unwrap() == "true" {
            let child_dir = Path::new(&directory).join("child");
            let result = std::process::Command::new(std::env::current_exe().unwrap())
                .args(["--exact", "tests::level_child", "--nocapture"])
                .env("FLATTRACE_TEST_LEVEL_DIRECTORY", &child_dir)
                .env("FLATTRACE_TEST_LEVEL_INHERIT", "false")
                .env(CONTEXT_ENV, w.traceparent(None).unwrap())
                // Command inherits FLATTRACE_LEVEL without overriding it.
                .output().unwrap();
            assert!(result.status.success(), "{} {}", String::from_utf8_lossy(&result.stdout), String::from_utf8_lossy(&result.stderr));
            let child_path = std::fs::read_dir(&child_dir).unwrap().next().unwrap().unwrap().path();
            let child: Value = serde_json::from_str(std::fs::read_to_string(child_path).unwrap().lines().next().unwrap()).unwrap();
            assert_eq!(child["trace"], w.trace);
            assert_eq!(child["parent"], w.root);
        }
        w.finish("ok").unwrap();
    }
    #[test]
    fn finish_with_carries_run_end_data() {
        let path = target();
        let w = Writer::create(&path, "test", "1", vec![], "test-host").unwrap();
        w.finish_with("ok", json!({"turns": 3, "tokens": null})).unwrap();
        let text = std::fs::read_to_string(&path).unwrap();
        let last: Value = serde_json::from_str(text.lines().last().unwrap()).unwrap();
        assert_eq!(last["event"], "run");
        assert_eq!(last["data"]["turns"], 3);
        assert!(last["data"]["tokens"].is_null());
        std::fs::remove_file(path).unwrap();
    }
    #[test]
    fn drop_leaves_run_unfinished_and_bad_context_is_rejected() {
        let path = target();
        { let _w = Writer::create(&path, "test", "1", vec![], "test-host").unwrap(); }
        assert_eq!(std::fs::read_to_string(&path).unwrap().lines().count(), 1);
        assert!(context("bad").is_err());
        assert!(context("00-00000000000000000000000000000000-1111111111111111-00").is_err());
        std::fs::remove_file(path).unwrap();
    }
}
