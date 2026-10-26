// SPDX-License-Identifier: MIT
//! Envelope validation and run-boundary checking, ported from
//! flattrace/check.py. Every issue string is identical to the Python
//! checker's; only malformed-JSON detail text after `line N:` /
//! `incomplete tail N:` is parser-specific (see docs/reader.md).

use serde_json::Value;
use std::collections::{BTreeMap, HashMap};
use std::fs::File;
use std::io::{self, BufRead, BufReader};
use std::path::Path;

fn is_hex_lower(value: &str, width: usize, nonzero: bool) -> bool {
    value.len() == width
        && value
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
        && (!nonzero || value.bytes().any(|c| c != b'0'))
}

fn valid_timestamp(value: &str) -> bool {
    let (core, _zone) = if let Some(stripped) = value.strip_suffix('Z') {
        (stripped, true)
    } else if let Some(stripped) = value.strip_suffix("+00:00") {
        (stripped, true)
    } else {
        return false;
    };
    let b = core.as_bytes();
    if b.len() < 19
        || b[4] != b'-'
        || b[7] != b'-'
        || b[10] != b'T'
        || b[13] != b':'
        || b[16] != b':'
    {
        return false;
    }
    for &i in &[0, 1, 2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 17, 18] {
        if !b[i].is_ascii_digit() {
            return false;
        }
    }
    let number = |a: usize, c: usize| {
        ((b[a] - b'0') as u32) * 10 + ((b[c] - b'0') as u32)
    };
    let (year, month, day) = (
        (b[0] - b'0') as u32 * 1000
            + (b[1] - b'0') as u32 * 100
            + (b[2] - b'0') as u32 * 10
            + (b[3] - b'0') as u32,
        number(5, 6),
        number(8, 9),
    );
    let (hour, minute, second) = (number(11, 12), number(14, 15), number(17, 18));
    if year < 1 {
        return false;
    }
    let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    let dim = match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 => {
            if leap {
                29
            } else {
                28
            }
        }
        _ => return false,
    };
    if !(1..=dim).contains(&day) || hour > 23 || minute > 59 || second > 59 {
        return false;
    }
    let rest = &b[19..];
    if rest.is_empty() {
        return true;
    }
    rest[0] == b'.' && rest.len() > 1 && rest[1..].iter().all(|c| c.is_ascii_digit())
}

/// Compare-friendly UTC instant; None when the timestamp is invalid.
/// civil-to-days uses the proleptic Gregorian calendar (no workload limits).
pub fn parse_timestamp(value: &str) -> Option<i128> {
    if !valid_timestamp(value) {
        return None;
    }
    let core = value.strip_suffix('Z').or_else(|| value.strip_suffix("+00:00"))?;
    let b = core.as_bytes();
    let digits = |a: usize, c: usize| ((b[a] - b'0') as i128) * 10 + ((b[c] - b'0') as i128);
    let year = (b[0] - b'0') as i128 * 1000
        + (b[1] - b'0') as i128 * 100
        + (b[2] - b'0') as i128 * 10
        + (b[3] - b'0') as i128;
    let (month, day) = (digits(5, 6), digits(8, 9));
    let (hour, minute, second) = (digits(11, 12), digits(14, 15), digits(17, 18));
    let nanos: i128 = if b.len() > 19 {
        let frac = &core[20..];
        let mut n: i128 = 0;
        for (i, c) in frac.bytes().take(9).enumerate() {
            n += ((c - b'0') as i128) * 10i128.pow(8 - i as u32);
        }
        n
    } else {
        0
    };
    let mut y = year;
    let m = month;
    y -= (14 - m) / 12;
    let era_day = (153 * (m + if m > 2 { -3 } else { 9 }) + 2) / 5 + day - 1;
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let doy = era_day + (365 * yoe + yoe / 4 - yoe / 100);
    let days = era * 146097 + doy - 719468;
    Some(days * 86_400_000_000_000 + hour * 3_600_000_000_000 + minute * 60_000_000_000 + second * 1_000_000_000 + nanos)
}

pub fn validate_event(row: &Value) -> Result<(), String> {
    let get = |key: &str| row.get(key);
    for key in ["v", "tool", "tool_version", "run", "trace", "span", "parent", "event", "state", "t", "data"] {
        if get(key).is_none() {
            return Err("missing envelope fields".to_string());
        }
    }
    if let Some(seq) = get("seq") {
        let ok = match seq {
            Value::Number(n) => n.as_u64().is_some(),
            _ => false,
        };
        if !ok {
            return Err("seq must be a nonnegative integer".to_string());
        }
    }
    match get("v") {
        Some(Value::Number(n)) if n.as_u64() == Some(1) => {}
        _ => return Err("v must be 1".to_string()),
    }
    for field in ["tool", "tool_version", "run", "event", "state", "t"] {
        if !get(field).map(|v| v.is_string()).unwrap_or(false) {
            return Err(format!("{field} must be a string"));
        }
    }
    let trace = get("trace").unwrap().as_str().unwrap();
    let span = get("span").unwrap().as_str().unwrap();
    if !is_hex_lower(trace, 32, true) {
        return Err("invalid trace".to_string());
    }
    if !is_hex_lower(span, 16, true) {
        return Err("invalid span".to_string());
    }
    match get("parent").unwrap() {
        Value::Null => {}
        Value::String(parent) if is_hex_lower(parent, 16, true) => {}
        _ => return Err("invalid parent".to_string()),
    }
    let event = get("event").unwrap().as_str().unwrap();
    let state = get("state").unwrap().as_str().unwrap();
    if event == "run" && state == "point" {
        return Err("run is reserved for lifetime spans".to_string());
    }
    if !["start", "end", "point"].contains(&state) || !get("data").unwrap().is_object() {
        return Err("invalid state or data".to_string());
    }
    if !valid_timestamp(get("t").unwrap().as_str().unwrap()) {
        return Err("t must be RFC3339 UTC".to_string());
    }
    if state == "end" {
        let outcome_ok = matches!(get("outcome"), Some(Value::String(_)));
        if get("took_ms").is_none() || !outcome_ok {
            return Err("end requires took_ms and outcome".to_string());
        }
        match get("took_ms") {
            Some(Value::Null) => {}
            Some(Value::Number(n))
                if n.as_f64().map(|f| f.is_finite() && f >= 0.0).unwrap_or(false) => {}
            _ => return Err("invalid took_ms".to_string()),
        }
    } else if get("took_ms").is_some() || get("outcome").is_some() {
        return Err("took_ms and outcome belong on ends".to_string());
    }
    if let Some(error) = row.get("error") {
        let ok = error.is_object()
            && matches!(error.get("kind"), Some(Value::String(_)))
            && matches!(error.get("message"), Some(Value::String(_)));
        if !ok {
            return Err("error requires string kind and message".to_string());
        }
    }
    Ok(())
}

pub struct LineReader {
    inner: Box<dyn BufRead>,
}

impl LineReader {
    pub fn open(path: &Path) -> io::Result<LineReader> {
        let file = File::open(path)?;
        let gz = path.to_string_lossy().ends_with(".gz");
        if gz {
            let decoder = flate2::read::MultiGzDecoder::new(BufReader::new(file));
            Ok(LineReader { inner: Box::new(BufReader::new(decoder)) })
        } else {
            Ok(LineReader { inner: Box::new(BufReader::new(file)) })
        }
    }
}

impl Iterator for LineReader {
    type Item = io::Result<(Vec<u8>, bool)>;
    fn next(&mut self) -> Option<io::Result<(Vec<u8>, bool)>> {
        let mut buf = Vec::new();
        match self.inner.read_until(b'\n', &mut buf) {
            Ok(0) => None,
            Ok(_) => {
                let newline = buf.ends_with(b"\n");
                Some(Ok((buf, newline)))
            }
            Err(error) => Some(Err(error)),
        }
    }
}

/// Read one trace file: validated rows plus the exact check.py issue strings.
fn str_field<'a>(row: &'a Value, key: &str) -> &'a str {
    row.get(key).and_then(|v| v.as_str()).unwrap_or("")
}

pub fn read_trace(path: &Path) -> io::Result<(Vec<Value>, Vec<String>)> {
    let mut rows: Vec<Value> = Vec::new();
    let mut issues: Vec<String> = Vec::new();
    let reader = LineReader::open(path)?;
    for (index, item) in reader.enumerate() {
        let number = index + 1;
        let (line, newline) = match item {
            Ok(pair) => pair,
            Err(error) => {
                issues.push(format!("incomplete gzip archive: {error}"));
                break;
            }
        };
        let text = match std::str::from_utf8(&line) {
            Ok(text) => text,
            Err(error) => {
                let prefix = if newline { "line" } else { "incomplete tail" };
                issues.push(format!("{prefix} {number}: {error}"));
                continue;
            }
        };
        let row: Value = match serde_json::from_str(text) {
            Ok(row) => row,
            Err(error) => {
                let prefix = if newline { "line" } else { "incomplete tail" };
                issues.push(format!("{prefix} {number}: {error}"));
                continue;
            }
        };
        match validate_event(&row) {
            Ok(()) => rows.push(row),
            Err(detail) => issues.push(format!("line {number}: {detail}")),
        }
    }
    if rows.is_empty() {
        issues.push("missing run start and end".to_string());
        return Ok((rows, issues));
    }
    let first = &rows[0];

    if first.get("event").and_then(|v| v.as_str()) != Some("run")
        || first.get("state").and_then(|v| v.as_str()) != Some("start")
    {
        issues.push("first line must be run start".to_string());
    }
    let header_ok = first
        .get("data")
        .and_then(|d| d.as_object())
        .map(|data| {
            let argv_ok = data.get("argv").map(|v| {
                v.as_array().map(|items| items.iter().all(|v| v.is_string())).unwrap_or(false)
            }).unwrap_or(false);
            let host_ok = data.get("host").map(|v| v.is_string()).unwrap_or(false);
            let version_ok = data.get("version").and_then(|v| v.as_str())
                == first.get("tool_version").and_then(|v| v.as_str());
            argv_ok && host_ok && version_ok
        })
        .unwrap_or(false);
    if !header_ok {
        issues.push("run start requires argv, host, version".to_string());
    }
    if let Some(data) = first.get("data").and_then(|v| v.as_object()) {
        for key in ["task", "variant"] {
            if let Some(value) = data.get(key) {
                if !value.is_string() {
                    issues.push(format!("run {key} must be a string"));
                }
            }
        }
    }
    for row in &rows {
        if ["run", "trace", "tool", "tool_version"]
            .iter()
            .any(|key| str_field(row, key) != str_field(first, key))
        {
            issues.push("file contains different runs or tools".to_string());
            break;
        }
    }
    let ends: Vec<&Value> = rows
        .iter()
        .filter(|row| {
            row.get("event").and_then(|v| v.as_str()) == Some("run")
                && row.get("state").and_then(|v| v.as_str()) == Some("end")
        })
        .collect();
    if ends.is_empty() {
        issues.push(format!(
            "unfinished run {}: missing run end",
            str_field(first, "run")
        ));
    } else if ends.len() != 1
        || ends[0] as *const Value != rows.last().map(|v| v as *const Value).unwrap()
        || str_field(ends[0], "span") != str_field(first, "span")
        || ends[0].get("parent") != first.get("parent")
    {
        issues.push("last line must be the matching run end".to_string());
    }
    let starts = rows
        .iter()
        .filter(|row| {
            row.get("event").and_then(|v| v.as_str()) == Some("run")
                && row.get("state").and_then(|v| v.as_str()) == Some("start")
        })
        .count();
    if starts != 1 {
        issues.push("expected one run start".to_string());
    }
    if rows.iter().any(|row| row.get("seq").is_some()) {
        for (expected, row) in rows.iter().enumerate() {
            let seq = row.get("seq").and_then(|v| v.as_u64());
            if seq != Some(expected as u64) {
                let got = row.get("seq").map(|v| v.to_string()).unwrap_or_else(|| "None".to_string());
                issues.push(format!("seq gap or reordering: expected {expected}, got {got}"));
            }
        }
    }
    let mut active: HashMap<String, (String, Option<String>)> = HashMap::new();
    let mut seen: HashMap<String, bool> = HashMap::new();
    for row in &rows {
        let span = str_field(row, "span").to_string();
        let state = str_field(row, "state").to_string();
        let parent = match row.get("parent") {
            Some(Value::String(s)) => Some(s.clone()),
            _ => None,
        };
        let identity = (str_field(row, "event").to_string(), parent.clone());
        if state == "start" {
            if seen.contains_key(&span) {
                issues.push(format!("duplicate span start {span}"));
            }
            if str_field(row, "event") != "run" && !active.contains_key(&parent.clone().unwrap_or_default()) {
                issues.push(format!("inactive parent for {span}"));
            }
            active.insert(span.clone(), identity);
            seen.insert(span, true);
        } else if state == "end" {
            if active.get(&span) != Some(&identity) {
                issues.push(format!("unmatched end {span}"));
            }
            active.remove(&span);
        } else if active.get(&span).map(|(_, p)| p) != Some(&parent) {
            issues.push(format!("point outside active span {span}"));
        }
    }
    let mut order: Vec<String> = Vec::new();
    for row in &rows {
        let span = str_field(row, "span").to_string();
        if active.contains_key(&span) && !order.contains(&span) {
            order.push(span);
        }
    }
    for span in order {
        issues.push(format!("unfinished span {span}: missing end"));
    }
    Ok((rows, issues))
}

/// Tool-owned event contract, mirroring read_events validation messages.
#[derive(Debug, Clone)]
pub struct Contract {
    pub tool: String,
    pub required: Vec<String>,
    pub commands: Option<BTreeMap<String, Vec<String>>>,
    pub events: BTreeMap<String, (String, i64)>,
}

pub fn read_events(path: &Path) -> Result<Contract, String> {
    let text = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
    let contract: Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
    let obj = contract.as_object().ok_or_else(|| "event list requires tool and events, with optional commands".to_string())?;
    let tool = obj.get("tool").and_then(|v| v.as_str()).ok_or_else(|| "event list requires tool and events, with optional commands".to_string())?;
    let events = obj.get("events").and_then(|v| v.as_object()).ok_or_else(|| "event list requires tool and events, with optional commands".to_string())?;
    if let Some(commands) = obj.get("commands") {
        if !commands.is_object() {
            return Err("event list requires tool and events, with optional commands".to_string());
        }
    }
    let mut declarations = BTreeMap::new();
    for (name, event) in events {
        let kind = event.get("kind").and_then(|v| v.as_str());
        let fields = event.get("fields").and_then(|v| v.as_object());
        let fields_ok = fields.map(|f| f.values().all(|v| v.is_string())).unwrap_or(false);
        if !matches!(kind, Some("span") | Some("point")) || !fields_ok {
            return Err(format!("invalid event declaration {name}"));
        }
        let level: i64 = match event.get("level") {
            None => 1,
            Some(Value::Number(n)) => match n.as_u64() {
                Some(v) if v >= 1 => v as i64,
                _ => return Err(format!("invalid level for event {name}")),
            },
            _ => return Err(format!("invalid level for event {name}")),
        };
        declarations.insert(name.clone(), (kind.unwrap().to_string(), level));
    }
    let check_names = |names: &Vec<String>, command: &str| -> Result<(), String> {
        if !names.iter().all(|n| declarations.contains_key(n)) {
            return Err(format!("invalid required events for {command}"));
        }
        Ok(())
    };
    let mut required = Vec::new();
    if let Some(names) = obj.get("required") {
        let list: Vec<String> = names.as_array().map(|items| {
            items.iter().filter_map(|v| v.as_str().map(|s| s.to_string())).collect()
        }).unwrap_or_default();
        if !names.is_array() || list.len() != names.as_array().unwrap().len() {
            return Err("invalid required events for required".to_string());
        }
        check_names(&list, "required")?;
        required = list;
    }
    let mut commands = None;
    if let Some(map) = obj.get("commands").and_then(|v| v.as_object()) {
        let mut table = BTreeMap::new();
        for (command, names) in map {
            let list: Vec<String> = names.as_array().map(|items| {
                items.iter().filter_map(|v| v.as_str().map(|s| s.to_string())).collect()
            }).unwrap_or_default();
            if !names.is_array() || list.len() != names.as_array().unwrap().len() {
                return Err(format!("invalid required events for {command}"));
            }
            check_names(&list, command)?;
            table.insert(command.clone(), list);
        }
        commands = Some(table);
    }
    Ok(Contract { tool: tool.to_string(), required, commands, events: declarations })
}

pub fn check_events(rows: &[Value], contract: &Contract, command: Option<&str>) -> Vec<String> {
    let mut issues = Vec::new();
    if rows.is_empty() {
        return issues;
    }
    let first = &rows[0];
    if first.get("tool").and_then(|v| v.as_str()) != Some(contract.tool.as_str()) {
        issues.push("event list tool mismatch".to_string());
    }
    let selected: Option<String> = match command {
        Some(id) => Some(id.to_string()),
        None => first.get("data").and_then(|d| d.get("command")).and_then(|v| v.as_str()).map(|s| s.to_string()),
    };
    let mut required = contract.required.clone();
    if let Some(commands) = &contract.commands {
        match selected.as_ref() {
            None => {
                issues.push("select a command with --command (or run data.command) for this contract".to_string());
                return issues;
            }
            Some(id) => match commands.get(id) {
                None => {
                    issues.push(format!("undeclared command '{id}'"));
                }
                Some(names) => {
                    required.extend(names.iter().cloned());
                }
            },
        }
    }
    let run_level: i64 = first
        .get("data")
        .and_then(|d| d.get("level"))
        .and_then(|v| v.as_i64())
        .unwrap_or(1);
    let declared = |name: &str| contract.events.get(name).map(|(_, level)| *level).unwrap_or(1);
    required.retain(|name| declared(name) <= run_level);
    let mut present = std::collections::HashSet::new();
    let mut over = std::collections::HashSet::new();
    for row in rows {
        let name = row.get("event").and_then(|v| v.as_str()).unwrap_or("");
        present.insert(name.to_string());
        if contract.events.contains_key(name) && declared(name) > run_level && !over.contains(name) {
            over.insert(name.to_string());
            issues.push(format!("level {} event {} in level {} run", declared(name), name, run_level));
        }
        match contract.events.get(name) {
            None => issues.push(format!("undeclared event {name}")),
            Some((kind, _)) => {
                let is_point = row.get("state").and_then(|v| v.as_str()) == Some("point");
                let want = if is_point { "point" } else { "span" };
                if kind != want {
                    issues.push(format!("wrong event kind {name}"));
                }
            }
        }
    }
    let selected_label = selected.clone().unwrap_or_else(|| "None".to_string());
    for name in required {
        if !present.contains(&name) {
            issues.push(format!("missing required event {name} for {selected_label}"));
        }
    }
    issues
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn timestamp_shapes() {
        assert!(valid_timestamp("2026-01-01T00:00:00Z"));
        assert!(valid_timestamp("2026-10-20T00:00:01.000Z"));
        assert!(valid_timestamp("2026-10-20T00:00:01.000+00:00"));
        assert!(!valid_timestamp("2026-13-01T00:00:00Z"));
        assert!(!valid_timestamp("2026-02-29T00:00:00Z"));
        assert!(valid_timestamp("2024-02-29T00:00:00Z"));
        assert!(!valid_timestamp("2026-01-01T24:00:00Z"));
        assert!(!valid_timestamp("2026-01-01T00:00:60Z"));
        assert!(!valid_timestamp("0000-01-01T00:00:00Z"));
        assert!(!valid_timestamp("2026-01-01 00:00:00Z"));
        assert!(!valid_timestamp("2026-01-01T00:00:00"));
        assert!(!valid_timestamp("2026-01-01T00:00:00+01:00"));
        assert!(!valid_timestamp("2026-1-1T00:00:00Z"));
    }

    #[test]
    fn timestamp_zone_equality_and_order() {
        assert_eq!(
            parse_timestamp("2026-10-20T00:00:01.000Z"),
            parse_timestamp("2026-10-20T00:00:01.000+00:00")
        );
        assert!(parse_timestamp("2026-10-20T00:00:01.050Z") > parse_timestamp("2026-10-20T00:00:01Z"));
        assert!(parse_timestamp("2026-10-21T00:00:00Z") > parse_timestamp("2026-10-20T23:59:59.999999999Z"));
        assert_eq!(parse_timestamp("1970-01-01T00:00:00Z"), Some(0));
    }

    #[test]
    fn envelope_rejects() {
        let mut row = serde_json::json!({
            "v": 1, "tool": "t", "tool_version": "1", "run": "r",
            "trace": "a".repeat(32), "span": "b".repeat(16), "parent": Value::Null,
            "event": "run", "state": "start", "t": "2026-01-01T00:00:00Z",
            "data": {"argv": [], "host": "h", "version": "1"},
        });
        assert!(validate_event(&row).is_ok());
        row["v"] = serde_json::json!(1.0);
        assert!(validate_event(&row).is_err());
        row["v"] = serde_json::json!(1);
        row["seq"] = serde_json::json!(-1);
        assert!(validate_event(&row).is_err());
    }
}
