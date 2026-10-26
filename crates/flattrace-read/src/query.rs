// SPDX-License-Identifier: MIT
//! D3 query surface over a loaded corpus: filters, group keys and the
//! check/spans/digest/group/tree/compare/overlap functions.
//! Selection reads typed in-memory span records in file and start order;
//! only quantile samples spill to SQLite.

use crate::pair::{Corpus, RunRec, SpanRec};
use crate::validate::parse_timestamp;
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap};
use std::io;

fn env_of(header: &Value) -> Value {
    header.get("env").cloned().unwrap_or(Value::Null)
}

fn get_path(data: &Value, dotted: &str) -> Value {
    let mut current = data;
    for part in dotted.split('.') {
        match current.get(part) {
            Some(next) => current = next,
            None => return Value::Null,
        }
    }
    current.clone()
}

/// Python-truthiness fallthrough, matching the reference `or` chains:
/// null, false, 0, "" and empty containers all fall through.
fn is_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(hit) => *hit,
        Value::Number(n) => n.as_f64().map(|v| v != 0.0).unwrap_or(false),
        Value::String(s) => !s.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

fn model_of(span: &SpanRec, env: &Value) -> Value {
    // Literal keys per docs/reader.md, with Python-truthiness fallback order:
    // span data["gen_ai.request.model"], then data["model"], then run env.
    for value in [span.data.get("gen_ai.request.model"), span.data.get("model")] {
        if let Some(value) = value {
            if is_truthy(value) {
                return value.clone();
            }
        }
    }
    match env.get("gen_ai.request.model") {
        Some(value) if is_truthy(value) => value.clone(),
        _ => Value::Null,
    }
}

fn session_of(span: &SpanRec, header: &Value) -> Value {
    for value in [span.data.get("session_id"), span.data.get("session"),
                  header.get("session_id"), header.get("session")] {
        if let Some(value) = value {
            if is_truthy(value) {
                return value.clone();
            }
        }
    }
    Value::Null
}

fn count_of(data: &Value) -> Value {
    match data.get("count") {
        Some(Value::Number(n)) if n.as_i64().map(|v| v >= 1).unwrap_or(false) => {
            Value::Number(n.as_i64().unwrap().into())
        }
        _ => json!(1),
    }
}

fn error_records(span: &SpanRec) -> Vec<BTreeMap<String, Value>> {
    let mut records = Vec::new();
    if !span.error.is_null() || span.outcome.as_str() == Some("error") {
        let error = if span.error.is_null() { &Value::Null } else { &span.error };
        records.push(BTreeMap::from([
            ("kind".to_string(), error.get("kind").cloned().unwrap_or(Value::Null)),
            ("message".to_string(), error.get("message").cloned().unwrap_or(Value::Null)),
            ("where".to_string(), get_path(&span.end_data, "where")),
            ("count".to_string(), count_of(&span.end_data)),
        ]));
    }
    for point in &span.points {
        if !point.error.is_null() {
            records.push(BTreeMap::from([
                ("kind".to_string(), point.error.get("kind").cloned().unwrap_or(Value::Null)),
                ("message".to_string(), point.error.get("message").cloned().unwrap_or(Value::Null)),
                ("where".to_string(), get_path(&point.data, "where")),
                ("count".to_string(), count_of(&point.data)),
            ]));
        }
    }
    records
}

fn error_summary(records: &[BTreeMap<String, Value>]) -> Value {
    let occurrences: i64 = records.iter().map(|r| r["count"].as_i64().unwrap_or(1)).sum();
    json!({ "records": records.len(), "occurrences": occurrences })
}

fn agent_of(run: &RunRec) -> Value {
    run.agent.clone().map(Value::String).unwrap_or(Value::Null)
}

fn span_value(span: &SpanRec, run: &RunRec, name: &str) -> Result<Value, String> {
    let header = &run.header;
    let env = env_of(header);
    match name {
        "outcome" => Ok(if span.unfinished { json!("unfinished") } else { span.outcome.clone() }),
        "agent" => Ok(agent_of(run)),
        "model" => Ok(model_of(span, &env)),
        "session" => Ok(session_of(span, header)),
        "tool" => Ok(Value::String(span.tool.clone())),
        "event" => Ok(Value::String(span.event.clone())),
        "trace" => Ok(Value::String(span.trace.clone())),
        "run" => Ok(Value::String(span.run.clone())),
        _ if name.starts_with("data.") => Ok(get_path(&span.data, &name[5..])),
        _ if name.starts_with("error.") => {
            let mut kinds: Vec<Value> = error_records(span).iter()
                .map(|r| r[&name[6..]].clone()).filter(|v| !v.is_null()).collect();
            kinds.sort_by(|a, b| a.to_string().cmp(&b.to_string()));
            kinds.dedup();
            Ok(match kinds.len() {
                0 => Value::Null,
                1 => kinds.into_iter().next().unwrap(),
                _ => Value::Array(kinds),
            })
        }
        _ => Err(format!("unknown group key {name:?}")),
    }
}

fn as_list(value: &Value) -> Vec<Value> {
    match value {
        Value::Array(items) => items.clone(),
        _ => vec![value.clone()],
    }
}

fn matches(span: &SpanRec, run: &RunRec, filt: &BTreeMap<String, Value>) -> Result<bool, String> {
    let header = &run.header;
    let env = env_of(header);
    for key in ["tool", "event", "trace", "run"] {
        if let Some(want) = filt.get(key) {
            let actual = match key {
                "tool" => Value::String(span.tool.clone()),
                "event" => Value::String(span.event.clone()),
                "trace" => Value::String(span.trace.clone()),
                _ => Value::String(span.run.clone()),
            };
            if !as_list(want).contains(&actual) {
                return Ok(false);
            }
        }
    }
    if let Some(want) = filt.get("outcome") {
        let actual = if span.unfinished { json!("unfinished") } else { span.outcome.clone() };
        if !as_list(want).contains(&actual) {
            return Ok(false);
        }
    }
    if let Some(Value::Bool(only)) = filt.get("unfinished") {
        if span.unfinished != *only {
            return Ok(false);
        }
    }
    if let Some(want) = filt.get("agent") {
        if &agent_of(run) != want {
            return Ok(false);
        }
    }
    if let Some(want) = filt.get("model") {
        if &model_of(span, &env) != want {
            return Ok(false);
        }
    }
    if let Some(want) = filt.get("session") {
        if &session_of(span, header) != want {
            return Ok(false);
        }
    }
    if let Some(want) = filt.get("error_kind") {
        if !error_records(span).iter().any(|r| &r["kind"] == want) {
            return Ok(false);
        }
    }
    match filt.get("t_from") {
        None => {}
        Some(Value::String(bound)) => {
            let start = parse_timestamp(&span.start_t).ok_or_else(|| format!("invalid t_from {bound:?}"))?;
            let floor = parse_timestamp(bound).ok_or_else(|| format!("invalid t_from {bound:?}"))?;
            if start < floor {
                return Ok(false);
            }
        }
        Some(other) => return Err(format!("invalid t_from {other}")),
    }
    match filt.get("t_to") {
        None => {}
        Some(Value::String(bound)) => {
            let start = parse_timestamp(&span.start_t).ok_or_else(|| format!("invalid t_to {bound:?}"))?;
            let ceil = parse_timestamp(bound).ok_or_else(|| format!("invalid t_to {bound:?}"))?;
            if start > ceil {
                return Ok(false);
            }
        }
        Some(other) => return Err(format!("invalid t_to {other}")),
    }
    if let Some(Value::Object(fields)) = filt.get("data") {
        for (dotted, want) in fields {
            if &get_path(&span.data, dotted) != want {
                return Ok(false);
            }
        }
    }
    Ok(true)
}

fn parse_filter(value: Option<&Value>) -> Result<BTreeMap<String, Value>, String> {
    match value {
        None | Some(Value::Null) => Ok(BTreeMap::new()),
        Some(Value::Object(map)) => Ok(map.iter().map(|(k, v)| (k.clone(), v.clone())).collect()),
        _ => Err("filter must be an object".to_string()),
    }
}

pub struct Selected<'a> {
    pub span: &'a SpanRec,
    pub run: &'a RunRec,
}

pub fn select_spans<'a>(corpus: &'a Corpus, filt: Option<&Value>) -> Result<Vec<Selected<'a>>, String> {
    let filt = parse_filter(filt)?;
    let mut out = Vec::new();
    for (run_position, run) in corpus.runs.iter().enumerate() {
        if run.duplicate {
            continue;
        }
        let mut members: Vec<Selected> = Vec::new();
        for &index in &corpus.spans_by_run[run_position] {
            let span = &corpus.spans[index];
            if matches(span, run, &filt)? {
                members.push(Selected { span, run });
            }
        }
        members.sort_by(|a, b| {
            let key = |s: &Selected| (!s.span.start_seq.is_null(), s.span.start_seq.as_u64().unwrap_or(0));
            key(a).partial_cmp(&key(b)).unwrap_or(std::cmp::Ordering::Equal)
        });
        out.extend(members);
    }
    Ok(out)
}

fn pointer(file: &str, start_seq: &Value, span: &str) -> Value {
    json!({ "file": file, "start_seq": start_seq, "span": span })
}

fn pointer_of(span: &SpanRec) -> Value {
    pointer(&span.file, &span.start_seq, &span.span)
}

pub fn coverage(corpus: &Corpus) -> Result<Value, String> {
    let mut runs = 0;
    let mut complete = 0;
    let mut invalid = 0;
    let mut duplicates = 0;
    let mut duplicates_with_issues = 0;
    let mut issues = 0;
    for run in &corpus.runs {
        if run.duplicate {
            duplicates += 1;
            if !run.issues_rendered.is_empty() {
                duplicates_with_issues += 1;
            }
            continue;
        }
        runs += 1;
        if run.status != "unfinished" {
            complete += 1;
        }
        for (category, count) in &run.issue_counts {
            if category != "line" && category != "incomplete tail"
                && category != "incomplete gzip archive" {
                issues += count;
            }
        }
    }
    for file in &corpus.files {
        if file.invalid {
            invalid += 1;
        }
    }
    let mut spans = 0;
    let mut unfinished_spans = 0;
    let mut points = 0;
    for span in &corpus.spans {
        if corpus.runs.get(span.run_idx).map(|r| r.duplicate).unwrap_or(true) {
            continue;
        }
        spans += 1;
        if span.unfinished {
            unfinished_spans += 1;
        }
        points += span.points.len() as i64;
    }
    let statement = format!(
        "{} files, {} runs ({} complete, {} unfinished, {} invalid), {} duplicate excluded, {} spans paired ({} unfinished), {} points, {} issues",
        corpus.files.len(), runs, complete, runs - complete, invalid,
        duplicates, spans, unfinished_spans, points, issues);
    Ok(json!({
        "files": corpus.files.len(), "runs": runs,
        "complete_runs": complete, "unfinished_runs": runs - complete,
        "invalid_files": invalid, "duplicates_excluded": duplicates,
        "duplicates_with_issues": duplicates_with_issues,
        "spans": spans, "spans_unfinished": unfinished_spans,
        "points": points, "issues": issues,
        "statement": statement,
    }))
}

fn span_json(span: &SpanRec) -> Value {
    let mut points = Vec::new();
    for point in &span.points {
        points.push(json!({
            "event": point.event, "t": point.t, "seq": point.seq,
            "data": point.data, "error": point.error,
        }));
    }
    json!({
        "file": span.file, "trace": span.trace, "run": span.run,
        "span": span.span, "parent": span.parent.clone().map(Value::String).unwrap_or(Value::Null),
        "tool": span.tool, "tool_version": span.tool_version, "event": span.event,
        "start_t": span.start_t, "start_seq": span.start_seq,
        "end_t": span.end_t.clone().map(Value::String).unwrap_or(Value::Null),
        "end_seq": span.end_seq,
        "took_ms": span.took, "outcome": span.outcome,
        "unfinished": span.unfinished, "depth": span.depth,
        "children": span.children, "data": span.data,
        "end_data": span.end_data, "error": span.error,
        "self_ms": span.self_ms,
        "self_unknown": span.self_unknown.clone().map(Value::String).unwrap_or(Value::Null),
        "points": points,
    })
}

pub fn spans(corpus: &Corpus, filt: Option<&Value>) -> Result<Value, String> {
    let selected = select_spans(corpus, filt)?;
    Ok(Value::Array(selected.iter().map(|s| span_json(s.span)).collect()))
}

fn nearest_or_null(summary: Option<crate::store::Summary>) -> Value {
    summary.map(|s| s.json()).unwrap_or(Value::Null)
}

pub fn digest(corpus: &Corpus, scope: Option<&Value>) -> Result<Value, String> {
    let selected = select_spans(corpus, scope)?;
    let store = &corpus.store;
    let id = store.tmp_id();
    let io_err = |e: io::Error| e.to_string();
    store.begin().map_err(io_err)?;
    let mut order: Vec<(String, String)> = Vec::new();
    let mut seen_key = std::collections::HashSet::new();
    for s in &selected {
        let key = (s.span.tool.clone(), s.span.event.clone());
        let gkey = serde_json::to_string(&[&key.0, &key.1]).unwrap();
        if seen_key.insert(gkey.clone()) {
            order.push(key);
        }
        store.insert_tmp(id, "took", &gkey, &s.span.took).map_err(io_err)?;
        if !s.span.self_ms.is_null() {
            store.insert_tmp(id, "self", &gkey, &s.span.self_ms).map_err(io_err)?;
        }
    }
    order.sort();
    let mut table = Vec::new();
    for (tool, event) in &order {
        let gkey = serde_json::to_string(&[tool, event]).unwrap();
        let members: Vec<&Selected> = selected.iter()
            .filter(|s| &s.span.tool == tool && &s.span.event == event).collect();
        let took = store.tmp_summary(id, "took", &gkey).map_err(io_err)?;
        let self_sum = store.tmp_summary(id, "self", &gkey).map_err(io_err)?;
        let mut reasons: BTreeMap<String, i64> = BTreeMap::new();
        let mut records = Vec::new();
        let mut unfinished_count = 0;
        let mut slowest: Option<&Selected> = None;
        for s in &members {
            if let Some(reason) = &s.span.self_unknown {
                *reasons.entry(reason.clone()).or_insert(0) += 1;
            }
            records.extend(error_records(&s.span));
            if s.span.unfinished {
                unfinished_count += 1;
            }
            if let Some(took) = s.span.took.as_f64() {
                let slower = slowest.map(|w: &Selected| w.span.took.as_f64().unwrap() < took).unwrap_or(true);
                if slower {
                    slowest = Some(s);
                }
            }
        }
        table.push(json!({
            "tool": tool, "event": event, "n": members.len(),
            "took": nearest_or_null(took), "self": nearest_or_null(self_sum),
            "self_unknown": reasons,
            "errors": error_summary(&records),
            "unfinished": unfinished_count,
            "slowest": slowest.map(|s| pointer_of(s.span)),
        }));
    }
    let errors: Vec<Value> = census_rows(&selected);
    let unfinished: Vec<Value> = selected.iter().filter(|s| s.span.unfinished)
        .map(|s| pointer_of(&s.span)).collect();
    store.clear_tmp().map_err(io_err)?;
    store.commit().map_err(io_err)?;
    Ok(json!({ "coverage": coverage(corpus)?, "table": table,
                "errors": errors, "unfinished": unfinished }))
}

fn census_rows(selected: &[Selected]) -> Vec<Value> {
    let mut census: BTreeMap<String, (Value, Value, Value, Value, i64, i64, Value, Value)> = BTreeMap::new();
    for s in selected {
        for r in error_records(&s.span) {
            let key = serde_json::to_string(&(&s.span.tool, &s.span.event, &r["kind"], &r["where"])).unwrap();
            let slot = census.entry(key).or_insert_with(|| {
                (Value::String(s.span.tool.clone()), Value::String(s.span.event.clone()),
                 r["kind"].clone(), r["where"].clone(), 0, 0,
                 r["message"].clone(), pointer_of(&s.span))
            });
            slot.4 += 1;
            slot.5 += r["count"].as_i64().unwrap_or(1);
        }
    }
    census.into_values().map(|(tool, event, kind, wh, records, occurrences, message, example)| {
        json!({ "tool": tool, "event": event, "kind": kind, "where": wh,
                "records": records, "occurrences": occurrences,
                "message": message, "example": example })
    }).collect()
}

fn py_str(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Number(n) => n.to_string(),
        Value::String(s) => s.clone(),
        Value::Array(items) => {
            let inner: Vec<String> = items.iter().map(|v| match v {
                Value::String(s) => format!("{s:?}").replace('\"', "'"),
                _ => py_str(v),
            }).collect();
            format!("[{}]", inner.join(", "))
        }
        Value::Object(_) => serde_json::to_string(value).unwrap_or_default(),
    }
}

pub fn group(corpus: &Corpus, by: &[String], metrics: Option<&Value>, filt: Option<&Value>) -> Result<Value, String> {
    let selected = select_spans(corpus, filt)?;
    let wanted: Vec<String> = match metrics {
        None | Some(Value::Null) => ["n", "took", "self", "errors", "unfinished", "points"].iter().map(|s| s.to_string()).collect(),
        Some(Value::Array(items)) => items.iter()
            .filter_map(|v| v.as_str())
            .filter(|m| ["n", "took", "self", "errors", "unfinished", "points"].contains(m))
            .map(|s| s.to_string()).collect(),
        _ => return Err("metrics must be an array".to_string()),
    };
    let mut buckets: BTreeMap<Vec<String>, Vec<usize>> = BTreeMap::new();
    let mut keys: HashMap<Vec<String>, Vec<Value>> = HashMap::new();
    for (index, s) in selected.iter().enumerate() {
        let mut key_values = Vec::new();
        let mut sort_key = Vec::new();
        for name in by {
            let value = span_value(&s.span, s.run, name)?;
            sort_key.push(py_str(&value));
            key_values.push(value);
        }
        buckets.entry(sort_key.clone()).or_default().push(index);
        keys.entry(sort_key).or_insert(key_values);
    }
    let store = &corpus.store;
    let id = store.tmp_id();
    let io_err = |e: io::Error| e.to_string();
    store.begin().map_err(io_err)?;
    for (sort_key, members) in &buckets {
        let gkey = serde_json::to_string(sort_key).unwrap();
        for index in members {
            let span = &selected[*index].span;
            store.insert_tmp(id, "took", &gkey, &span.took).map_err(io_err)?;
            if !span.self_ms.is_null() {
                store.insert_tmp(id, "self", &gkey, &span.self_ms).map_err(io_err)?;
            }
        }
    }
    let mut groups = Vec::new();
    for (sort_key, members) in &buckets {
        let gkey = serde_json::to_string(sort_key).unwrap();
        let key_values = &keys[sort_key];
        let mut entry = serde_json::Map::new();
        let mut key_map = serde_json::Map::new();
        for (name, value) in by.iter().zip(key_values.iter()) {
            key_map.insert(name.clone(), value.clone());
        }
        entry.insert("key".to_string(), Value::Object(key_map));
        if wanted.contains(&"n".to_string()) {
            entry.insert("n".to_string(), json!(members.len()));
        }
        if wanted.contains(&"took".to_string()) {
            entry.insert("took".to_string(), nearest_or_null(store.tmp_summary(id, "took", &gkey).map_err(io_err)?));
        }
        if wanted.contains(&"self".to_string()) {
            entry.insert("self".to_string(), nearest_or_null(store.tmp_summary(id, "self", &gkey).map_err(io_err)?));
        }
        if wanted.contains(&"errors".to_string()) {
            let records: Vec<BTreeMap<String, Value>> = members.iter()
                .flat_map(|i| error_records(&selected[*i].span)).collect();
            entry.insert("errors".to_string(), error_summary(&records));
        }
        if wanted.contains(&"unfinished".to_string()) {
            entry.insert("unfinished".to_string(),
                json!(members.iter().filter(|i| selected[**i].span.unfinished).count()));
        }
        if wanted.contains(&"points".to_string()) {
            entry.insert("points".to_string(),
                json!(members.iter().map(|i| selected[*i].span.points.len()).sum::<usize>()));
        }
        groups.push(Value::Object(entry));
    }
    store.clear_tmp().map_err(io_err)?;
    store.commit().map_err(io_err)?;
    Ok(json!({ "coverage": coverage(corpus)?, "by": by, "groups": groups }))
}

pub fn tree(corpus: &Corpus, trace: &str) -> Result<Value, String> {
    let mut run_order: Vec<usize> = Vec::new();
    for (index, run) in corpus.runs.iter().enumerate() {
        if run.trace == trace && !run.duplicate {
            run_order.push(index);
        }
    }
    let mut run_spans: HashMap<usize, Vec<(String, Option<String>)>> = HashMap::new();
    for &idx in &run_order {
        let mut list = Vec::new();
        for &i in &corpus.spans_by_run[idx] {
            let span = &corpus.spans[i];
            list.push((span.span.clone(), span.parent.clone()));
        }
        run_spans.insert(idx, list);
    }
    let mut nodes: HashMap<usize, Value> = HashMap::new();
    for &idx in &run_order {
        let run = &corpus.runs[idx];
        let root_parent = corpus.spans_by_run[idx].iter()
            .map(|&i| &corpus.spans[i])
            .find(|s| s.event == "run")
            .and_then(|s| s.parent.clone());
        let unfinished_spans = run.unfinished.values().sum::<i64>();
        let points: i64 = corpus.spans_by_run[idx].iter()
            .map(|&i| corpus.spans[i].points.len() as i64).sum();
        nodes.insert(idx, json!({
            "run": run.run, "file": run.path, "tool": run.tool,
            "status": run.status, "took_ms": run.took, "outcome": run.status,
            "parent_span": root_parent.clone().map(Value::String).unwrap_or(Value::Null),
            "parent_run": Value::Null, "parent_outside_corpus": false,
            "spans": run_spans.get(&idx).map(|s| s.len()).unwrap_or(0),
            "unfinished_spans": unfinished_spans, "points": points,
            "failed": run.status == "error", "children": Vec::<Value>::new(),
        }));
    }
    let owner_of = |parent_span: &str| -> Option<usize> {
        for &idx in &run_order {
            if let Some(spans) = run_spans.get(&idx) {
                if spans.iter().any(|(span, _)| span == parent_span) {
                    return Some(idx);
                }
            }
        }
        None
    };
    let mut parent_link: HashMap<usize, Option<usize>> = HashMap::new();
    let mut outside: HashMap<usize, bool> = HashMap::new();
    for &idx in &run_order {
        let parent_span = nodes[&idx].get("parent_span").and_then(|v| v.as_str()).map(|s| s.to_string());
        match parent_span.and_then(|p| owner_of(&p)) {
            Some(owner) => { parent_link.insert(idx, Some(owner)); }
            None => {
                if nodes[&idx].get("parent_span").map(|v| !v.is_null()).unwrap_or(false) {
                    outside.insert(idx, true);
                }
                parent_link.insert(idx, None);
            }
        }
    }
    let mut children_of: HashMap<usize, Vec<usize>> = HashMap::new();
    for &idx in &run_order {
        if let Some(Some(owner)) = parent_link.get(&idx) {
            children_of.entry(*owner).or_default().push(idx);
        }
    }
    fn build(idx: usize, nodes: &HashMap<usize, Value>,
             parent_link: &HashMap<usize, Option<usize>>,
             outside: &HashMap<usize, bool>,
             children_of: &HashMap<usize, Vec<usize>>) -> Value {
        let mut node = nodes[&idx].clone();
        if let Some(Some(owner)) = parent_link.get(&idx) {
            node["parent_run"] = nodes[owner].get("run").cloned().unwrap_or(Value::Null);
        }
        if outside.get(&idx).cloned().unwrap_or(false) {
            node["parent_outside_corpus"] = json!(true);
        }
        let mut children: Vec<Value> = children_of.get(&idx).cloned().unwrap_or_default()
            .iter().map(|child| build(*child, nodes, parent_link, outside, children_of)).collect();
        sort_by_took(&mut children);
        node["children"] = Value::Array(children);
        node
    }
    fn sort_by_took(items: &mut [Value]) {
        items.sort_by(|a, b| {
            let key = |v: &Value| {
                let took = v.get("took_ms").cloned().unwrap_or(Value::Null);
                (took.is_null(), took.as_f64().map(|t| -t).unwrap_or(0.0),
                 v.get("run").and_then(|r| r.as_str()).unwrap_or("").to_string())
            };
            key(a).partial_cmp(&key(b)).unwrap_or(std::cmp::Ordering::Equal)
        });
    }
    let mut roots: Vec<Value> = run_order.iter()
        .filter(|idx| parent_link.get(*idx).map(|o| o.is_none()).unwrap_or(true))
        .map(|idx| build(*idx, &nodes, &parent_link, &outside, &children_of)).collect();
    sort_by_took(&mut roots);
    let mut failed: Vec<Value> = run_order.iter()
        .filter(|idx| nodes[*idx].get("failed").and_then(|v| v.as_bool()).unwrap_or(false))
        .map(|idx| Value::String(corpus.runs[*idx].run.clone())).collect();
    failed.sort_by(|a, b| a.as_str().cmp(&b.as_str()));
    let mut slowest: Option<String> = None;
    let mut slowest_took = f64::NEG_INFINITY;
    for &idx in &run_order {
        if let Some(took) = nodes[&idx].get("took_ms").and_then(|v| v.as_f64()) {
            if slowest.is_none() || took > slowest_took {
                slowest = Some(corpus.runs[idx].run.clone());
                slowest_took = took;
            }
        }
    }
    Ok(json!({ "coverage": coverage(corpus)?, "trace": trace,
                "roots": roots, "failed": failed,
                "slowest": slowest.map(Value::String).unwrap_or(Value::Null) }))
}

fn side_summary(store: &crate::store::Store, id: u64, members: &[&Selected]) -> Result<Value, String> {
    let gkey = serde_json::to_string(&(&members[0].span.tool, &members[0].span.event)).unwrap();
    let records: Vec<BTreeMap<String, Value>> = members.iter()
        .flat_map(|s| error_records(&s.span)).collect();
    Ok(json!({
        "n": members.len(),
        "took": nearest_or_null(store.tmp_summary(id, "took", &gkey).map_err(|e| e.to_string())?),
        "errors": error_summary(&records),
        "unfinished": members.iter().filter(|s| s.span.unfinished).count(),
    }))
}

fn subtract(left: &Value, right: &Value) -> Value {
    match (left.as_i64(), right.as_i64()) {
        (Some(a), Some(b)) => a.checked_sub(b).map(|v| Value::Number(v.into())).unwrap_or(Value::Null),
        _ => match (left.as_f64(), right.as_f64()) {
            (Some(a), Some(b)) => serde_json::Number::from_f64(a - b).map(Value::Number).unwrap_or(Value::Null),
            _ => Value::Null,
        },
    }
}

pub fn compare(a: &Corpus, b: &Corpus, filt: Option<&Value>) -> Result<Value, String> {
    fn side(corpus: &Corpus, filt: Option<&Value>) -> Result<Value, String> {
        let selected = select_spans(corpus, filt)?;
        let store = &corpus.store;
        let id = store.tmp_id();
        store.begin().map_err(|e: io::Error| e.to_string())?;
        let mut order: Vec<(String, String)> = Vec::new();
        let mut seen = std::collections::HashSet::new();
        for s in &selected {
            let key = (s.span.tool.clone(), s.span.event.clone());
            let gkey = serde_json::to_string(&key).unwrap();
            if seen.insert(gkey.clone()) {
                order.push(key);
            }
            store.insert_tmp(id, "took", &gkey, &s.span.took).map_err(|e| e.to_string())?;
        }
        order.sort();
        let mut events = Vec::new();
        for (tool, event) in &order {
            let members: Vec<&Selected> = selected.iter()
                .filter(|s| &s.span.tool == tool && &s.span.event == event).collect();
            events.push(json!({ "tool": tool, "event": event,
                                "summary": side_summary(store, id, &members)? }));
        }
        let runs: Vec<Value> = corpus.runs.iter().filter(|r| !r.duplicate).map(|run| {
            json!({ "run": run.run, "tool": run.tool,
                     "outcome": run.status, "took_ms": run.took })
        }).collect();
        store.clear_tmp().map_err(|e: io::Error| e.to_string())?;
        store.commit().map_err(|e: io::Error| e.to_string())?;
        Ok(json!({ "coverage": coverage(corpus)?, "runs": runs, "events": events }))
    }
    let aside = side(a, filt)?;
    let bside = side(b, filt)?;
    let mut a_map: BTreeMap<(String, String), Value> = BTreeMap::new();
    let mut b_map: BTreeMap<(String, String), Value> = BTreeMap::new();
    for e in aside.get("events").and_then(|v| v.as_array()).cloned().unwrap_or_default() {
        a_map.insert((e["tool"].as_str().unwrap_or("").to_string(),
                      e["event"].as_str().unwrap_or("").to_string()), e["summary"].clone());
    }
    for e in bside.get("events").and_then(|v| v.as_array()).cloned().unwrap_or_default() {
        b_map.insert((e["tool"].as_str().unwrap_or("").to_string(),
                      e["event"].as_str().unwrap_or("").to_string()), e["summary"].clone());
    }
    let mut keys: Vec<(String, String)> = a_map.keys().chain(b_map.keys()).cloned().collect();
    keys.sort();
    keys.dedup();
    let mut events = Vec::new();
    for (tool, event) in keys {
        let a_sum = a_map.get(&(tool.clone(), event.clone()));
        let b_sum = b_map.get(&(tool.clone(), event.clone()));
        let mut entry = serde_json::Map::new();
        entry.insert("tool".to_string(), Value::String(tool));
        entry.insert("event".to_string(), Value::String(event));
        entry.insert("a".to_string(), a_sum.cloned().unwrap_or(Value::Null));
        entry.insert("b".to_string(), b_sum.cloned().unwrap_or(Value::Null));
        let mut delta_n = Value::Null;
        let mut delta_p50 = Value::Null;
        let mut delta_total = Value::Null;
        if let (Some(a), Some(b)) = (a_sum, b_sum) {
            delta_n = json!(b["n"].as_i64().unwrap_or(0) - a["n"].as_i64().unwrap_or(0));
            if !a["took"].is_null() && !b["took"].is_null() {
                delta_p50 = subtract(&b["took"]["p50"], &a["took"]["p50"]);
                delta_total = subtract(&b["took"]["total"], &a["took"]["total"]);
            }
        }
        entry.insert("delta_n".to_string(), delta_n);
        entry.insert("delta_p50".to_string(), delta_p50);
        entry.insert("delta_total".to_string(), delta_total);
        events.push(Value::Object(entry));
    }
    Ok(json!({ "a": aside, "b": bside, "events": events }))
}

pub fn overlap(corpus: &Corpus, window_event: &str, span_filter: Option<&Value>) -> Result<Value, String> {
    let pairs = select_spans(corpus, None)?;
    let members = select_spans(corpus, span_filter)?;
    let mut windows: Vec<&Selected> = pairs.iter()
        .filter(|s| s.span.event == window_event && s.span.event != "run").collect();
    windows.sort_by(|a, b| (a.span.start_t.clone(), a.span.start_seq.as_u64().unwrap_or(0))
        .partial_cmp(&(b.span.start_t.clone(), b.span.start_seq.as_u64().unwrap_or(0)))
        .unwrap_or(std::cmp::Ordering::Equal));
    let members: Vec<&Selected> = members.iter()
        .filter(|s| s.span.event != "run" && s.span.event != window_event).collect();
    let mut bounded = Vec::new();
    let mut unbounded = Vec::new();
    for window in windows {
        let mut record = serde_json::Map::new();
        record.insert("window".to_string(), pointer_of(&window.span));
        record.insert("start_t".to_string(), Value::String(window.span.start_t.clone()));
        record.insert("end_t".to_string(), window.span.end_t.clone().map(Value::String).unwrap_or(Value::Null));
        if window.span.end_t.is_none() {
            unbounded.push(Value::Object(record));
            continue;
        }
        let start = crate::validate::parse_timestamp(&window.span.start_t).unwrap_or(0);
        let end = window.span.end_t.as_deref().and_then(crate::validate::parse_timestamp).unwrap_or(0);
        let mut hits: Vec<(bool, i64, String, Value)> = Vec::new();
        for other in &members {
            let other_start = crate::validate::parse_timestamp(&other.span.start_t).unwrap_or(0);
            let covers = match other.span.end_t.as_deref().and_then(crate::validate::parse_timestamp) {
                Some(other_end) => other_start <= end && other_end >= start,
                None => other_start <= end,
            };
            if covers {
                let seq_none = other.span.start_seq.is_null();
                let seq_num = other.span.start_seq.as_u64().unwrap_or(0) as i64;
                let mut hit = serde_json::Map::new();
                hit.insert("file".to_string(), Value::String(other.span.file.clone()));
                hit.insert("start_seq".to_string(), other.span.start_seq.clone());
                hit.insert("span".to_string(), Value::String(other.span.span.clone()));
                hit.insert("event".to_string(), Value::String(other.span.event.clone()));
                hit.insert("took_ms".to_string(), other.span.took.clone());
                hit.insert("outcome".to_string(), other.span.outcome.clone());
                hit.insert("unfinished".to_string(), Value::Bool(other.span.unfinished));
                hits.push((seq_none, seq_num, other.span.span.clone(), Value::Object(hit)));
            }
        }
        hits.sort_by(|a, b| (a.0, a.1, &a.2).partial_cmp(&(b.0, b.1, &b.2)).unwrap_or(std::cmp::Ordering::Equal));
        record.insert("overlapping".to_string(),
            Value::Array(hits.into_iter().map(|(_, _, _, v)| v).collect()));
        bounded.push(Value::Object(record));
    }
    unbounded.sort_by(|a, b| {
        let key = |v: &Value| (v["start_t"].as_str().unwrap_or("").to_string(),
                               v["window"]["start_seq"].as_u64().unwrap_or(0));
        key(a).partial_cmp(&key(b)).unwrap_or(std::cmp::Ordering::Equal)
    });
    Ok(json!({ "coverage": coverage(corpus)?, "window_event": window_event,
                "windows": bounded, "unbounded_windows": unbounded }))
}
