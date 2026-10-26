// SPDX-License-Identifier: MIT
//! Streaming corpus load: validate, pair and spill only quantile samples.
//! The corpus lives in memory as typed Rust structs (parsed once, never
//! re-serialized); only took/self samples spill to the temporary SQLite
//! file, mirroring flattrace.stats. No memory cap and no size threshold.

use crate::store::Store;
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
        Some(Value::Number(n)) if n.as_i64().map(|v| v >= 1).unwrap_or(false) => {
            n.as_i64().unwrap_or(1)
        }
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
    points: Vec<PointRec>,
}

pub struct PointRec {
    pub event: String,
    pub t: String,
    pub seq: Value,
    pub data: Value,
    pub error: Value,
}

pub struct SpanRec {
    pub run_idx: usize,
    pub file: String,
    pub span: String,
    pub parent: Option<String>,
    pub event: String,
    pub tool: String,
    pub tool_version: String,
    pub trace: String,
    pub run: String,
    pub start_t: String,
    pub start_seq: Value,
    pub end_t: Option<String>,
    pub end_seq: Value,
    pub took: Value,
    pub took_num: Option<f64>,
    pub outcome: Value,
    pub unfinished: bool,
    pub depth: i64,
    pub children: Vec<String>,
    pub data: Value,
    pub end_data: Value,
    pub error: Value,
    pub self_ms: Value,
    pub self_unknown: Option<String>,
    pub points: Vec<PointRec>,
}

pub struct RunRec {
    pub path: String,
    pub trace: String,
    pub run: String,
    pub tool: String,
    pub tool_version: String,
    pub status: String,
    pub took: Value,
    pub agent: Option<String>,
    pub header: Value,
    pub level: String,
    pub events: usize,
    pub issues_rendered: Vec<String>,
    pub issue_counts: BTreeMap<String, i64>,
    pub unfinished: BTreeMap<String, i64>,
    pub unknown: BTreeMap<String, BTreeMap<String, i64>>,
    pub duplicate: bool,
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
    pub runs: Vec<RunRec>,
    pub spans: Vec<SpanRec>,
    pub spans_by_run: Vec<Vec<usize>>,
    pub errors: HashMap<String, i64>,
    pub warnings: HashMap<String, i64>,
}

/// Group per-occurrence issue strings the way flattrace.stats.IssueLog does.
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
        let mut runs = Vec::new();
        let mut spans = Vec::new();
        let mut spans_by_run: Vec<Vec<usize>> = Vec::new();
        let mut errors = HashMap::new();
        let mut warnings = HashMap::new();
        let mut seen: HashSet<(String, String)> = HashSet::new();
        for path in paths {
            let mut loader = FileLoader {
                path, store: &store, seen: &mut seen, errors: &mut errors, warnings: &mut warnings,
                runs: &mut runs, spans: &mut spans, spans_by_run: &mut spans_by_run,
            };
            files.push(loader.load_file()?);
        }
        store.commit()?;
        Ok(Corpus { store, files, runs, spans, spans_by_run, errors, warnings })
    }

    pub fn primary_runs(&self) -> Vec<usize> {
        self.runs.iter().enumerate()
            .filter(|(_, run)| !run.duplicate)
            .map(|(index, _)| index)
            .collect()
    }
}

struct FileLoader<'a> {
    path: &'a str,
    store: &'a Store,
    seen: &'a mut HashSet<(String, String)>,
    errors: &'a mut HashMap<String, i64>,
    warnings: &'a mut HashMap<String, i64>,
    runs: &'a mut Vec<RunRec>,
    spans: &'a mut Vec<SpanRec>,
    spans_by_run: &'a mut Vec<Vec<usize>>,
}

impl<'a> FileLoader<'a> {
    fn load_file(&mut self) -> io::Result<FileRec> {
        let path: &str = self.path;
        let fs_path = Path::new(path);
        let reader = LineReader::open(fs_path)?;
        let mut categorized: Vec<(String, String)> = Vec::new();
        let mut active: HashMap<String, Frame> = HashMap::new();
        let mut seen_spans: HashSet<String> = HashSet::new();
        let mut expected_seq: u64 = 0;
        let mut has_seq = false;
        let mut missing_seq = false;
        let mut events: usize = 0;
        let mut run_idx: Option<usize> = None;
        let mut duplicate = false;
        let mut root: Option<String> = None;
        let mut last: Option<(String, String, String)> = None;
        let mut first_row: Option<Value> = None;
        let mut row_count = 0;
        let mut unknown: HashMap<(String, String), i64> = HashMap::new();
        let mut unfinished: HashMap<String, i64> = HashMap::new();
        let mut status = "unfinished".to_string();
        let mut run_took = Value::Null;
        let mut run_header = Value::Null;
        let mut run_agent: Option<String> = None;
        let mut run_level = "unknown".to_string();

        for (index, item) in reader.enumerate() {
            let number = index + 1;
            let (line, newline) = match item {
                Ok(pair) => pair,
                Err(error) => {
                    categorized.push(("incomplete gzip archive".to_string(),
                        format!("incomplete gzip archive: {error}")));
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
            if first_row.is_none() {
                first_row = Some(row.clone());
                let id = (str_field(&row, "trace").to_string(), str_field(&row, "run").to_string());
                duplicate = self.seen.contains(&id);
                self.seen.insert(id);
                if str_field(&row, "event") == "run" && str_field(&row, "state") == "start" {
                    root = Some(str_field(&row, "span").to_string());
                    run_level = row.get("data").and_then(|d| d.get("level"))
                        .and_then(|v| v.as_i64()).filter(|v| *v >= 0)
                        .map(|v| v.to_string()).unwrap_or_else(|| "unknown".to_string());
                } else {
                    categorized.push(("first valid line must be run start".to_string(),
                        "first valid line must be run start".to_string()));
                }
                run_agent = row.get("data").and_then(|d| d.get("env"))
                    .and_then(|e| e.get("agent.name")).and_then(|v| v.as_str()).map(|s| s.to_string());
                run_header = row.get("data").cloned().unwrap_or(Value::Null);
                run_idx = Some(self.runs.len());
                self.spans_by_run.push(Vec::new());
            }
            let first = first_row.as_ref().unwrap();
            if ["run", "trace", "tool", "tool_version"].iter().any(|key| str_field(&row, key) != str_field(first, key)) {
                categorized.push(("mixed runs or tools".to_string(),
                    format!("line {number}: file contains different runs or tools")));
                continue;
            }
            events += 1;
            if row.get("seq").is_some() {
                has_seq = true;
                if row.get("seq").and_then(|v| v.as_u64()) != Some(expected_seq) || missing_seq {
                    categorized.push(("seq gap or reordering".to_string(),
                        format!("line {number}: seq gap or reordering")));
                }
            } else {
                missing_seq = true;
                if has_seq {
                    categorized.push(("missing seq".to_string(), format!("line {number}: missing seq")));
                }
            }
            expected_seq += 1;
            last = Some((
                str_field(&row, "event").to_string(),
                str_field(&row, "state").to_string(),
                str_field(&row, "span").to_string(),
            ));
            let data = row.get("data").cloned().unwrap_or(Value::Null);
            let severity = data.get("severity").and_then(|v| v.as_str()).unwrap_or("");
            let count = count_of(&data);
            if !duplicate {
                if row.get("error").is_some() || row.get("outcome").and_then(|v| v.as_str()) == Some("error") || severity == "error" {
                    *self.errors.entry(str_field(&row, "event").to_string()).or_insert(0) += count;
                }
                if severity == "warning" || severity == "warn" {
                    *self.warnings.entry(str_field(&row, "event").to_string()).or_insert(0) += count;
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
                    categorized.push(("duplicate span start".to_string(),
                        format!("line {number}: duplicate span start")));
                    continue;
                }
                if name == "run" && Some(&span) != root.as_ref() {
                    categorized.push(("unexpected run start".to_string(),
                        format!("line {number}: unexpected run start")));
                }
                if Some(&span) != root.as_ref() && !active.contains_key(parent.as_deref().unwrap_or("")) {
                    categorized.push(("inactive parent".to_string(), format!("line {number}: inactive parent")));
                }
                if let Some(owner) = parent.as_deref().and_then(|p| active.get_mut(p)) {
                    if !owner.children.is_empty() {
                        owner.reason = Some("overlapping children".to_string());
                    }
                    owner.children.insert(span.clone());
                    owner.permanent.push(span.clone());
                }
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
                        categorized.push(("unmatched end".to_string(),
                            format!("line {number}: unmatched end")));
                        continue;
                    }
                };
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
                self.finish_span(&span, Frame {
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
                }, &row, &total, self_ms, reason.clone(), run_idx.unwrap(), path, duplicate)?;
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
                    categorized.push(("point outside active span".to_string(),
                        format!("line {number}: point outside active span")));
                    continue;
                }
                active.get_mut(&span).unwrap().points.push(PointRec {
                    event: name,
                    t: str_field(&row, "t").to_string(),
                    seq: row.get("seq").cloned().unwrap_or(Value::Null),
                    data,
                    error: row.get("error").cloned().unwrap_or(Value::Null),
                });
            }
        }
        let first = match first_row {
            Some(first) => first,
            None => {
                categorized.push(("missing run start and end".to_string(),
                    "missing run start and end".to_string()));
                return Ok(FileRec {
                    path: path.to_string(), rows: 0, issues: categorized,
                    duplicate, invalid: true,
                });
            }
        };
        if root.is_some() {
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
                categorized.push(("run start requires argv, host, version".to_string(),
                    "run start requires argv, host, version".to_string()));
            }
        }
        let expected_last = root.clone().map(|span| ("run".to_string(), "end".to_string(), span));
        if root.is_none() || last != expected_last {
            categorized.push(("missing final matching run end".to_string(),
                "missing final matching run end".to_string()));
        }
        let had_unfinished = !active.is_empty();
        let mut unfinished_spans: Vec<(String, Frame)> = Vec::new();
        for (span, frame) in active.drain() {
            *unfinished.entry(frame.event.clone()).or_insert(0) += 1;
            unfinished_spans.push((span, frame));
        }
        unfinished_spans.sort_by(|a, b| a.0.cmp(&b.0));
        for (span, frame) in unfinished_spans {
            self.finish_unfinished(&span, frame, run_idx.unwrap(), &first)?;
        }
        if had_unfinished {
            categorized.push(("unfinished spans".to_string(), "unfinished spans".to_string()));
        }
        if !categorized.is_empty() {
            self.nuke_self(run_idx.unwrap(), &mut unknown)?;
        }
        let (rendered, counts) = render_issues(&categorized);
        let mut unknown_map: BTreeMap<String, BTreeMap<String, i64>> = BTreeMap::new();
        for ((name, reason), count) in unknown {
            unknown_map.entry(name).or_default().insert(reason, count);
        }
        let mut unfinished_map: BTreeMap<String, i64> = BTreeMap::new();
        for (name, count) in unfinished {
            unfinished_map.insert(name, count);
        }
        self.runs.push(RunRec {
            path: path.to_string(),
            trace: str_field(&first, "trace").to_string(),
            run: str_field(&first, "run").to_string(),
            tool: str_field(&first, "tool").to_string(),
            tool_version: str_field(&first, "tool_version").to_string(),
            status,
            took: run_took,
            agent: run_agent,
            header: run_header,
            level: run_level,
            events,
            issues_rendered: rendered,
            issue_counts: counts,
            unfinished: unfinished_map,
            unknown: unknown_map,
            duplicate,
        });
        Ok(FileRec { path: path.to_string(), rows: row_count, issues: categorized, duplicate, invalid: false })
    }

    #[allow(clippy::too_many_arguments)]
    fn finish_span(&mut self, span: &str, frame: Frame, row: &Value, total: &Value,
        self_ms: Option<f64>, reason: Option<String>, run_idx: usize, path: &str,
        duplicate: bool) -> io::Result<()> {
        let mut children = frame.permanent.clone();
        children.sort();
        let error = row.get("error").cloned().unwrap_or(Value::Null);
        self.spans.push(SpanRec {
            run_idx,
            file: path.to_string(),
            span: span.to_string(),
            parent: frame.parent.as_str().map(|s| s.to_string()),
            event: frame.event.clone(),
            tool: row.get("tool").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            tool_version: row.get("tool_version").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            trace: row.get("trace").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            run: row.get("run").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            start_t: frame.start_t.clone(),
            start_seq: frame.start_seq.clone(),
            end_t: row.get("t").and_then(|v| v.as_str()).map(|s| s.to_string()),
            end_seq: row.get("seq").cloned().unwrap_or(Value::Null),
            took: total.clone(),
            took_num: total.as_f64(),
            outcome: row.get("outcome").cloned().unwrap_or(Value::Null),
            unfinished: false,
            depth: frame.depth,
            children,
            data: frame.data.clone(),
            end_data: row.get("data").cloned().unwrap_or(Value::Null),
            error,
            self_ms: self_ms.and_then(serde_json::Number::from_f64).map(Value::Number).unwrap_or(Value::Null),
            self_unknown: reason.clone(),
            points: frame.points,
        });
        let index = self.spans.len() - 1;
        self.spans_by_run[run_idx].push(index);
        if !duplicate {
            let event = self.spans[index].event.clone();
            if !total.is_null() {
                self.store.insert_sample(run_idx, "took", &event, total)?;
            }
            if let Some(value) = self_ms {
                self.store.insert_sample(run_idx, "self", &event, &json!(value))?;
            }
        }
        Ok(())
    }

    fn finish_unfinished(&mut self, span: &str, frame: Frame, run_idx: usize, first: &Value) -> io::Result<()> {
        let mut children = frame.permanent.clone();
        children.sort();
        self.spans.push(SpanRec {
            run_idx,
            file: self.path.to_string(),
            span: span.to_string(),
            parent: frame.parent.as_str().map(|s| s.to_string()),
            event: frame.event.clone(),
            tool: str_field(&first, "tool").to_string(),
            tool_version: str_field(&first, "tool_version").to_string(),
            trace: str_field(&first, "trace").to_string(),
            run: str_field(&first, "run").to_string(),
            start_t: frame.start_t.clone(),
            start_seq: frame.start_seq.clone(),
            end_t: None,
            end_seq: Value::Null,
            took: Value::Null,
            took_num: None,
            outcome: Value::Null,
            unfinished: true,
            depth: frame.depth,
            children,
            data: frame.data.clone(),
            end_data: Value::Null,
            error: Value::Null,
            self_ms: Value::Null,
            self_unknown: Some("unfinished".to_string()),
            points: frame.points,
        });
        let index = self.spans.len() - 1;
        self.spans_by_run[run_idx].push(index);
        Ok(())
    }

    fn nuke_self(&mut self, run_idx: usize, unknown: &mut HashMap<(String, String), i64>) -> io::Result<()> {
        for index in self.spans_by_run[run_idx].clone() {
            let span = &mut self.spans[index];
            if !span.self_ms.is_null() {
                *unknown.entry((span.event.clone(), "incomplete or invalid run".to_string())).or_insert(0) += 1;
                span.self_ms = Value::Null;
                span.self_unknown = Some("incomplete or invalid run".to_string());
            }
        }
        self.store.delete_samples(&format!("{run_idx}\u{1f}self"))?;
        Ok(())
    }
}
