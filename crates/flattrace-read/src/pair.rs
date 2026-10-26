// SPDX-License-Identifier: MIT
//! Streaming corpus load: validate, pair and spill to SQLite.
//! Mirrors flattrace.stats._read_run pairing/self/unknown policy; memory
//! holds only active spans and small counters, never samples or rows.

use crate::store::{SpanInsert, Store};
use crate::validate::{validate_event, LineReader};
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::io;
use std::path::Path;

fn str_field<'a>(row: &'a Value, key: &str) -> &'a str {
    row.get(key).and_then(|v| v.as_str()).unwrap_or("")
}

fn count_of(data: &Value) -> i64 {
    match data.get("count") {
        Some(Value::Number(n)) => n.as_i64().filter(|&v| v >= 1).unwrap_or(1),
        _ => 1,
    }
}

struct Frame {
    event: String,
    parent: Value,
    depth: i64,
    children: HashSet<String>,
    permanent: Vec<String>,
    child_ms: f64,
    reason: Option<String>,
    start_t: String,
    start_seq: Value,
    data: Value,
    points: Vec<Value>,
}

pub struct FileRec {
    pub path: String,
    pub rows: usize,
    pub issues: Vec<(String, String)>,
    pub duplicate: bool,
    pub invalid: bool,
}

pub struct Corpus {
    pub store: Store,
    pub files: Vec<FileRec>,
    pub errors: HashMap<String, i64>,
    pub warnings: HashMap<String, i64>,
}

struct SpanParts {
    trace: String,
    run: String,
    tool: String,
    tool_version: String,
}

/// Group per-occurrence issue strings the way flattrace.stats.IssueLog does:
/// fixed categories keep their first detail; line/tail/archive issues group
/// by prefix. Returns (rendered list, category counts).
pub fn render_issues(categorized: &[(String, String)]) -> (Vec<String>, BTreeMap<String, i64>) {
    let mut counts: BTreeMap<String, i64> = BTreeMap::new();
    let mut first: HashMap<String, String> = HashMap::new();
    let mut order: Vec<String> = Vec::new();
    for (category, detail) in categorized {
        if !counts.contains_key(category) {
            order.push(category.clone());
            first.insert(category.clone(), detail.clone());
        }
        *counts.entry(category.clone()).or_insert(0) += 1;
    }
    let rendered = order.iter().map(|category| {
        let count = counts[category];
        if count > 1 {
            format!("{} (occurrences: {count})", first[category])
        } else {
            first[category].clone()
        }
    }).collect();
    (rendered, counts)
}


impl Corpus {
    pub fn load(paths: &[String]) -> io::Result<Corpus> {
        let store = Store::open()?;
        store.begin()?;
        let mut files = Vec::new();
        let mut errors = HashMap::new();
        let mut warnings = HashMap::new();
        let mut seen: HashSet<(String, String)> = HashSet::new();
        for path in paths {
            let rec = load_file(path, &store, &mut seen, &mut errors, &mut warnings)?;
            files.push(rec);
        }
        store.commit()?;
        Ok(Corpus { store, files, errors, warnings })
    }

    pub fn primary_run_ids(&self) -> io::Result<Vec<i64>> {
        self.store.primary_runs()
    }
}

#[allow(clippy::too_many_arguments)]
fn load_file(
    path: &str,
    store: &Store,
    seen: &mut HashSet<(String, String)>,
    errors: &mut HashMap<String, i64>,
    warnings: &mut HashMap<String, i64>,
) -> io::Result<FileRec> {
    let fs_path = Path::new(path);
    let reader = LineReader::open(fs_path)?;
    let mut categorized: Vec<(String, String)> = Vec::new();
    let mut active: HashMap<String, Frame> = HashMap::new();
    let mut seen_spans: HashSet<String> = HashSet::new();
    let mut frames: HashMap<String, SpanParts> = HashMap::new();
    let mut expected_seq: u64 = 0;
    let mut has_seq = false;
    let mut missing_seq = false;
    let mut events: usize = 0;
    let mut run_idx: Option<i64> = None;
    let mut duplicate = false;
    let mut root: Option<String> = None;
    let mut first_row: Option<Value> = None;
    let mut last: Option<(String, String, String)> = None;
    let mut row_count = 0;
    let mut unknown: HashMap<(String, String), i64> = HashMap::new();
    let mut unfinished: HashMap<String, i64> = HashMap::new();
    let mut status = "unfinished".to_string();
    let mut run_took = Value::Null;

    for (index, item) in reader.enumerate() {
        let number = index + 1;
        let (line, newline) = match item {
            Ok(pair) => pair,
            Err(error) => {
                categorized.push(("incomplete gzip archive".to_string(), format!("incomplete gzip archive: {error}")));
                break;
            }
        };
        let text = match std::str::from_utf8(&line) {
            Ok(text) => text,
            Err(error) => {
                let prefix = if newline { "line" } else { "incomplete tail" };
                categorized.push((prefix.to_string(), format!("{prefix} {number}: {error}")));
                continue;
            }
        };
        let row: Value = match serde_json::from_str(text) {
            Ok(row) => row,
            Err(error) => {
                let prefix = if newline { "line" } else { "incomplete tail" };
                categorized.push((prefix.to_string(), format!("{prefix} {number}: {error}")));
                continue;
            }
        };
        if let Err(detail) = validate_event(&row) {
            categorized.push(("line".to_string(), format!("line {number}: {detail}")));
            continue;
        }
        last = Some((
            str_field(&row, "event").to_string(),
            str_field(&row, "state").to_string(),
            str_field(&row, "span").to_string(),
        ));
        if first_row.is_none() {
            first_row = Some(row.clone());
            let id = (str_field(&row, "trace").to_string(), str_field(&row, "run").to_string());
            duplicate = seen.contains(&id);
            seen.insert(id);
            if str_field(&row, "event") == "run" && str_field(&row, "state") == "start" {
                root = Some(str_field(&row, "span").to_string());
            }
            let agent = row
                .get("data")
                .and_then(|d| d.get("env"))
                .and_then(|e| e.get("agent.name"))
                .and_then(|v| v.as_str())
                .map(|s| s.to_string());
            let level = row
                .get("data")
                .and_then(|d| d.get("level"))
                .and_then(|v| v.as_i64())
                .filter(|v| *v >= 0)
                .map(|v| v.to_string())
                .unwrap_or_else(|| "unknown".to_string());
            let header = row.get("data").cloned().unwrap_or(Value::Null);
            run_idx = Some(store.insert_run(
                path,
                str_field(&row, "trace"),
                str_field(&row, "run"),
                str_field(&row, "tool"),
                str_field(&row, "tool_version"),
                agent.as_deref(),
                &header,
                &level,
                duplicate,
            )?);
        }
        let first = first_row.as_ref().unwrap();
        if ["run", "trace", "tool", "tool_version"].iter().any(|key| str_field(&row, key) != str_field(first, key)) {
            categorized.push(("mixed runs or tools".to_string(), format!("line {number}: file contains different runs or tools")));
            continue;
        }
        events += 1;
        if row.get("seq").is_some() {
            has_seq = true;
            if row.get("seq").and_then(|v| v.as_u64()) != Some(expected_seq) || missing_seq {
                categorized.push(("seq gap or reordering".to_string(), format!("line {number}: seq gap or reordering")));
            }
        } else {
            missing_seq = true;
            if has_seq {
                categorized.push(("missing seq".to_string(), format!("line {number}: missing seq")));
            }
        }
        expected_seq += 1;
        let data = row.get("data").cloned().unwrap_or(Value::Null);
        let severity = data.get("severity").and_then(|v| v.as_str()).unwrap_or("");
        let count = count_of(&data);
        if !duplicate {
            if row.get("error").is_some() || row.get("outcome").and_then(|v| v.as_str()) == Some("error") || severity == "error" {
                *errors.entry(str_field(&row, "event").to_string()).or_insert(0) += count;
            }
            if severity == "warning" || severity == "warn" {
                *warnings.entry(str_field(&row, "event").to_string()).or_insert(0) += count;
            }
        }
        let span = str_field(&row, "span").to_string();
        let parent_value = row.get("parent").cloned().unwrap_or(Value::Null);
        let parent = parent_value.as_str().map(|s| s.to_string());
        let name = str_field(&row, "event").to_string();
        let state = str_field(&row, "state").to_string();
        row_count += 1;
        if state == "start" {
            if seen_spans.contains(&span) {
                categorized.push(("duplicate span start".to_string(), format!("line {number}: duplicate span start")));
                continue;
            }
            if name == "run" && Some(&span) != root.as_ref() {
                categorized.push(("unexpected run start".to_string(), format!("line {number}: unexpected run start")));
            }
            if name != "run" && !active.contains_key(parent.as_deref().unwrap_or("")) {
                categorized.push(("inactive parent".to_string(), format!("line {number}: inactive parent")));
            }
            if let Some(owner) = parent.as_deref().and_then(|p| active.get_mut(p)) {
                if !owner.children.is_empty() {
                    owner.reason = Some("overlapping children".to_string());
                }
                owner.children.insert(span.clone());
                owner.permanent.push(span.clone());
            }
            frames.insert(span.clone(), SpanParts {
                trace: str_field(&row, "trace").to_string(),
                run: str_field(&row, "run").to_string(),
                tool: str_field(&row, "tool").to_string(),
                tool_version: str_field(&row, "tool_version").to_string(),
            });
            active.insert(span.clone(), Frame {
                event: name.clone(),
                parent: parent_value.clone(),
                depth: active.len() as i64,
                children: HashSet::new(),
                permanent: Vec::new(),
                child_ms: 0.0,
                reason: None,
                start_t: str_field(&row, "t").to_string(),
                start_seq: row.get("seq").cloned().unwrap_or(Value::Null),
                data: data.clone(),
                points: Vec::new(),
            });
            seen_spans.insert(span);
        } else if state == "end" {
            let mut frame = match active.remove(&span) {
                Some(frame) if frame.event == name && frame.parent == parent_value => frame,
                _ => {
                    categorized.push(("unmatched end".to_string(), format!("line {number}: unmatched end")));
                    continue;
                }
            };
            frames.remove(&span);
            let total = row.get("took_ms").cloned().unwrap_or(Value::Null);
            let total_f = total.as_f64();
            let mut reason = frame.reason.take();
            if !frame.children.is_empty() {
                reason = Some("child outlives parent".to_string());
                let mut ancestor = parent.clone();
                while let Some(key) = ancestor {
                    match active.get_mut(&key) {
                        Some(owner) => {
                            owner.reason = Some("async descendant".to_string());
                            ancestor = owner.parent.as_str().map(|s| s.to_string());
                        }
                        None => break,
                    }
                }
            }
            if total_f.is_none() {
                reason = Some("unknown duration".to_string());
            } else if frame.child_ms > total_f.unwrap() {
                reason = Some("children exceed parent duration".to_string());
            }
            let self_ms = match (&reason, total_f) {
                (None, Some(total)) => Some(total - frame.child_ms),
                _ => None,
            };
            if let Some(reason) = reason.clone() {
                *unknown.entry((name.clone(), reason)).or_insert(0) += 1;
            }
            finish_span(&span, &Frame {
                event: name.clone(),
                parent: parent_value.clone(),
                depth: frame.depth,
                children: HashSet::new(),
                permanent: std::mem::take(&mut frame.permanent),
                child_ms: 0.0,
                reason: None,
                start_t: frame.start_t.clone(),
                start_seq: frame.start_seq.clone(),
                data: frame.data.clone(),
                points: std::mem::take(&mut frame.points),
            }, &row, &total, self_ms, reason.clone(), path, run_idx.unwrap(), store, duplicate)?;
            if let Some(owner_span) = parent.clone() {
                if let Some(owner) = active.get_mut(&owner_span) {
                    owner.children.remove(&span);
                    match total_f {
                        None => owner.reason = Some("unknown child duration".to_string()),
                        Some(total) => owner.child_ms += total,
                    }
                }
            }
            if Some(&span) == root.as_ref() {
                status = row.get("outcome").and_then(|v| v.as_str()).unwrap_or("unfinished").to_string();
                run_took = total.clone();
            }
        } else {
            let inside = active.get(&span).map(|f| f.parent == parent_value).unwrap_or(false);
            if !inside {
                categorized.push(("point outside active span".to_string(), format!("line {number}: point outside active span")));
                continue;
            }
            let point = json!({"event": name, "t": str_field(&row, "t"),
                "seq": row.get("seq").cloned().unwrap_or(Value::Null),
                "data": data,
                "error": row.get("error").cloned().unwrap_or(Value::Null)});
            active.get_mut(&span).unwrap().points.push(point);
        }
    }
    let first = match first_row {
        Some(first) => first,
        None => {
            categorized.push(("missing run start and end".to_string(), "missing run start and end".to_string()));
            return Ok(FileRec { path: path.to_string(), rows: 0, issues: categorized, duplicate, invalid: true });
        }
    };
    if str_field(&first, "event") != "run" || str_field(&first, "state") != "start" {
        categorized.push(("first valid line must be run start".to_string(), "first valid line must be run start".to_string()));
    }
    let header_ok = first.get("data").and_then(|d| d.as_object()).map(|data| {
        let argv_ok = data.get("argv").map(|v| {
            v.as_array().map(|items| items.iter().all(|v| v.is_string())).unwrap_or(false)
        }).unwrap_or(false);
        let host_ok = data.get("host").map(|v| v.is_string()).unwrap_or(false);
        let version_ok = data.get("version").and_then(|v| v.as_str())
            == first.get("tool_version").and_then(|v| v.as_str());
        argv_ok && host_ok && version_ok
    }).unwrap_or(false);
    if !header_ok {
        categorized.push(("run start requires argv, host, version".to_string(), "run start requires argv, host, version".to_string()));
    }
    // The run end must be the last valid row of this run, mirroring stats.py:
    // no parent comparison here and no check-only "unfinished run" message.
    let expected_last = root.clone().map(|span| ("run".to_string(), "end".to_string(), span));
    if root.is_none() || last != expected_last {
        categorized.push(("missing final matching run end".to_string(), "missing final matching run end".to_string()));
    }
    for (span, frame) in active.iter() {
        *unfinished.entry(frame.event.clone()).or_insert(0) += 1;
        let parts = frames.get(span).unwrap();
        finish_span_parts(span, parts, frame, path, run_idx.unwrap(), store)?;
    }
    if !active.is_empty() {
        categorized.push(("unfinished spans".to_string(), "unfinished spans".to_string()));
    }
    let (rendered, counts) = render_issues(&categorized);
    if !categorized.is_empty() {
        for entry in store.spans_with_self(run_idx.unwrap())? {
            *unknown.entry((entry.0, "incomplete or invalid run".to_string())).or_insert(0) += entry.1;
        }
        store.nuke_self(run_idx.unwrap())?;
    }
    let unknown_json = {
        let mut map = serde_json::Map::new();
        let mut by_name: HashMap<String, serde_json::Map<String, Value>> = HashMap::new();
        let mut names: Vec<String> = Vec::new();
        for ((name, reason), count) in &unknown {
            if !by_name.contains_key(name) {
                names.push(name.clone());
            }
            by_name.entry(name.clone()).or_default().insert(reason.clone(), json!(*count));
        }
        names.sort();
        for name in names {
            map.insert(name.clone(), Value::Object(by_name.remove(&name).unwrap()));
        }
        Value::Object(map)
    };
    let mut unfinished_names: Vec<String> = unfinished.keys().cloned().collect();
    unfinished_names.sort();
    let mut unfinished_map = serde_json::Map::new();
    for name in unfinished_names {
        unfinished_map.insert(name.clone(), json!(unfinished[&name]));
    }
    store.finish_run(run_idx.unwrap(), &status, &run_took, &unknown_json,
        &Value::Object(unfinished_map), &rendered, &counts, events as i64)?;
    let invalid = false;
    Ok(FileRec { path: path.to_string(), rows: row_count, issues: categorized, duplicate, invalid })
}

fn insert_samples(store: &Store, run_idx: i64, event: &str,
    took: &Value, self_ms: Option<f64>, duplicate: bool) -> io::Result<()> {
    if duplicate {
        return Ok(());
    }
    if !took.is_null() {
        store.insert_sample(run_idx, "took", event, took)?;
    }
    if let Some(value) = self_ms {
        store.insert_sample(run_idx, "self", event, &json!(value))?;
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn finish_span(span: &str, frame: &Frame, row: &Value, total: &Value, self_ms: Option<f64>,
    reason: Option<String>, path: &str, run_idx: i64, store: &Store,
    duplicate: bool) -> io::Result<()> {
    let children: Vec<String> = {
        let mut list = frame.permanent.clone();
        list.sort();
        list
    };
    let error = row.get("error").cloned().unwrap_or(Value::Null);
    let took_num = total.as_f64();
    store.insert_span(SpanInsert {
        run_idx,
        file: path,
        span,
        parent_json: &frame.parent,
        event: &frame.event,
        tool: row.get("tool").and_then(|v| v.as_str()).unwrap_or(""),
        tool_version: row.get("tool_version").and_then(|v| v.as_str()).unwrap_or(""),
        trace: row.get("trace").and_then(|v| v.as_str()).unwrap_or(""),
        run: row.get("run").and_then(|v| v.as_str()).unwrap_or(""),
        start_t: &frame.start_t,
        start_seq: &frame.start_seq,
        end_t: Some(row.get("t").cloned().unwrap_or(Value::Null)),
        end_seq: row.get("seq").cloned(),
        took: total,
        took_num,
        outcome: &row.get("outcome").cloned().unwrap_or(Value::Null),
        unfinished: false,
        depth: frame.depth,
        children: &children,
        data: &frame.data,
        end_data: Some(row.get("data").cloned().unwrap_or(Value::Null)),
        error: &error,
        self_ms: &self_ms.and_then(serde_json::Number::from_f64).map(Value::Number).unwrap_or(Value::Null),
        self_unknown: &reason.map(Value::String).unwrap_or(Value::Null),
        points: &frame.points,
    })?;
    insert_samples(store, run_idx, &frame.event, total, self_ms, duplicate)?;
    Ok(())
}

fn finish_span_parts(span: &str, parts: &SpanParts, frame: &Frame, path: &str, run_idx: i64,
    store: &Store) -> io::Result<()> {
    let children: Vec<String> = {
        let mut list = frame.permanent.clone();
        list.sort();
        list
    };
    let null = Value::Null;
    let self_unknown = Value::String("unfinished".to_string());
    store.insert_span(SpanInsert {
        run_idx,
        file: path,
        span,
        parent_json: &frame.parent,
        event: &frame.event,
        tool: &parts.tool,
        tool_version: &parts.tool_version,
        trace: &parts.trace,
        run: &parts.run,
        start_t: &frame.start_t,
        start_seq: &frame.start_seq,
        end_t: None,
        end_seq: None,
        took: &null,
        took_num: None,
        outcome: &null,
        unfinished: true,
        depth: frame.depth,
        children: &children,
        data: &frame.data,
        end_data: None,
        error: &null,
        self_ms: &null,
        self_unknown: &self_unknown,
        points: &frame.points,
    })?;
    Ok(())
}