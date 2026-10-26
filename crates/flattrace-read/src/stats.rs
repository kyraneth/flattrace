// SPDX-License-Identifier: MIT
//! Corpus statistics with the exact flattrace.stats report shape.

use crate::pair::{render_issues, Corpus};
use rusqlite::params;
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::io;

fn parse_json(text: &str) -> Value {
    serde_json::from_str(text).unwrap_or(Value::Null)
}

pub fn stats(corpus: &Corpus) -> Result<Value, String> {
    let err = |e: rusqlite::Error| e.to_string();
    let conn = corpus.store.connection();
    let run_rows: Vec<(i64, String, String, String, String, String, String, String, String, String, String, String, String, i64)> = {
        let mut stmt = conn.prepare(
            "SELECT idx, path, trace, run, tool, tool_version, status, took, level, unfinished, unknown_json, issues_rendered, issue_counts, events FROM run WHERE duplicate = 0 ORDER BY idx").map_err(err)?;
        let out: Vec<(i64, String, String, String, String, String, String, String, String, String, String, String, String, i64)> = stmt.query_map([], |row: &rusqlite::Row| {
            Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?, row.get(5)?,
                row.get(6)?, row.get(7)?, row.get(8)?, row.get(9)?, row.get(10)?,
                row.get(11)?, row.get(12)?, row.get(13)?))
        }).map_err(err)?.collect::<Result<Vec<_>, _>>().map_err(err)?;
        out
    };
    let mut runs_out = Vec::new();
    let mut took_list: Vec<Value> = Vec::new();
    for (idx, path, trace, run, tool, tool_version, status, took_json, level,
         unfinished_json, unknown_json, rendered_json, counts_json, events) in &run_rows {
        let took: Value = parse_json(took_json);
        took_list.push(took.clone());
        let mut names: Vec<String> = {
            let mut stmt = conn.prepare("SELECT DISTINCT gkey FROM sample WHERE kind = ?1").map_err(err)?;
            let out: Vec<String> = stmt.query_map(params![format!("{idx}\u{1f}took")], |r: &rusqlite::Row| r.get::<_, String>(0))
                .map_err(err)?.collect::<Result<Vec<_>, _>>().map_err(err)?;
            out
        };
        let unknown: Value = parse_json(unknown_json);
        if let Some(obj) = unknown.as_object() {
            for key in obj.keys() {
                if !names.contains(key) {
                    names.push(key.clone());
                }
            }
        }
        let unfinished: Value = parse_json(unfinished_json);
        if let Some(obj) = unfinished.as_object() {
            for key in obj.keys() {
                if !names.contains(key) {
                    names.push(key.clone());
                }
            }
        }
        names.sort();
        let mut spans_map = serde_json::Map::new();
        let mut totals: BTreeMap<String, f64> = BTreeMap::new();
        for name in &names {
            let total_sum = corpus.store.summary(*idx, "took", name).map_err(|e| e.to_string())?;
            let self_sum = corpus.store.summary(*idx, "self", name).map_err(|e| e.to_string())?;
            if let Some(summary) = &total_sum {
                totals.insert(name.clone(), summary.total.as_f64().unwrap_or(0.0));
            }
            spans_map.insert(name.clone(), json!({
                "total_ms": total_sum.map(|s| s.json_float()).unwrap_or(Value::Null),
                "self_ms": self_sum.map(|s| s.json_float()).unwrap_or(Value::Null),
            }));
        }
        let mut dominant: Vec<String> = names.iter().filter(|n| *n != "run").cloned().collect();
        dominant.sort_by(|a, b| totals.get(b).unwrap_or(&0.0)
            .partial_cmp(totals.get(a).unwrap_or(&0.0)).unwrap_or(std::cmp::Ordering::Equal));
        runs_out.push(json!({
            "path": path, "trace": trace, "run": run, "tool": tool,
            "tool_version": tool_version, "status": status, "took_ms": took,
            "level": level, "events": events,
            "issues": parse_json(rendered_json),
            "issue_counts": parse_json(counts_json),
            "unfinished_spans": unfinished,
            "self_unknown": unknown,
            "spans": spans_map, "dominant_spans": dominant,
        }));
    }
    let mut duplicates_out = Vec::new();
    for file in corpus.files.iter().filter(|f| f.duplicate) {
        let mut stmt = conn.prepare(
            "SELECT trace, run, status, issues_rendered, issue_counts FROM run WHERE path = ?1").map_err(err)?;
        let (trace, run, status, rendered, counts): (String, String, String, String, String) =
            stmt.query_row(params![file.path], |row: &rusqlite::Row| {
                Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?))
            }).map_err(err)?;
        duplicates_out.push(json!({
            "path": file.path, "trace": trace, "run": run, "status": status,
            "issues": parse_json(&rendered), "issue_counts": parse_json(&counts),
        }));
    }
    let mut invalid_out = Vec::new();
    for file in corpus.files.iter().filter(|f| f.invalid) {
        let (rendered, counts) = render_issues(&file.issues);
        let mut counts_map = serde_json::Map::new();
        for (category, count) in &counts {
            counts_map.insert(category.clone(), json!(*count));
        }
        invalid_out.push(json!({
            "path": file.path, "trace": Value::Null, "run": Value::Null,
            "tool": Value::Null, "status": "invalid", "took_ms": Value::Null,
            "level": "unknown", "events": 0,
            "issues": rendered, "issue_counts": counts_map,
            "unfinished_spans": serde_json::Map::new(),
            "self_unknown": serde_json::Map::new(),
        }));
    }
    let tmp = corpus.store.tmp_id();
    corpus.store.begin().map_err(|e: io::Error| e.to_string())?;
    let mut all_names: Vec<String> = {
        let mut stmt = conn.prepare(
            "SELECT DISTINCT s.event FROM span s JOIN run r ON r.idx = s.run_idx AND r.duplicate = 0").map_err(err)?;
        let out: Vec<String> = stmt.query_map([], |r: &rusqlite::Row| r.get::<_, String>(0)).map_err(err)?
            .collect::<Result<Vec<_>, _>>().map_err(err)?;
        out
    };
    {
        let mut stmt = conn.prepare(
            "SELECT s.event, s.took, s.self_ms FROM span s JOIN run r ON r.idx = s.run_idx AND r.duplicate = 0").map_err(err)?;
        let rows = stmt.query_map([], |row: &rusqlite::Row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?, row.get::<_, String>(2)?))
        }).map_err(err)?.collect::<Result<Vec<_>, _>>().map_err(err)?;
        for (event, took_json, self_json) in rows {
            let took: Value = parse_json(&took_json);
            if !all_names.contains(&event) {
                all_names.push(event.clone());
            }
            corpus.store.insert_tmp(tmp, "took", &event, &took).map_err(|e| e.to_string())?;
            let self_value: Value = parse_json(&self_json);
            if !self_value.is_null() {
                corpus.store.insert_tmp(tmp, "self", &event, &self_value).map_err(|e| e.to_string())?;
            }
        }
    }
    all_names.sort();
    let mut spans_out = serde_json::Map::new();
    for name in &all_names {
        let total_sum = corpus.store.tmp_summary(tmp, "took", name).map_err(|e| e.to_string())?;
        let self_sum = corpus.store.tmp_summary(tmp, "self", name).map_err(|e| e.to_string())?;
        spans_out.insert(name.clone(), json!({
            "total_ms": total_sum.map(|s| s.json_float()).unwrap_or(Value::Null),
            "self_ms": self_sum.map(|s| s.json_float()).unwrap_or(Value::Null),
        }));
    }
    corpus.store.clear_tmp().map_err(|e: io::Error| e.to_string())?;
    corpus.store.commit().map_err(|e: io::Error| e.to_string())?;
    let mut statuses: BTreeMap<String, i64> = BTreeMap::new();
    let mut levels: BTreeMap<String, i64> = BTreeMap::new();
    for row in &run_rows {
        *statuses.entry(row.6.clone()).or_insert(0) += 1;
        *levels.entry(row.8.clone()).or_insert(0) += 1;
    }
    let mut slowest: Vec<usize> = (0..run_rows.len()).collect();
    slowest.sort_by(|a, b| {
        let key = |i: &usize| {
            let took = &took_list[*i];
            (took.is_null(), took.as_f64().map(|t| -t).unwrap_or(0.0))
        };
        key(a).partial_cmp(&key(b)).unwrap_or(std::cmp::Ordering::Equal)
    });
    let mut errors_map = serde_json::Map::new();
    let mut error_keys: Vec<String> = corpus.errors.keys().cloned().collect();
    error_keys.sort();
    for key in error_keys {
        errors_map.insert(key.clone(), json!(corpus.errors[&key]));
    }
    let mut warnings_map = serde_json::Map::new();
    let mut warning_keys: Vec<String> = corpus.warnings.keys().cloned().collect();
    warning_keys.sort();
    for key in warning_keys {
        warnings_map.insert(key.clone(), json!(corpus.warnings[&key]));
    }
    Ok(json!({
        "files": corpus.files.len(), "run_count": runs_out.len(),
        "statuses": statuses, "levels": levels,
        "duplicates": duplicates_out, "invalid_files": invalid_out,
        "spans": spans_out,
        "errors_by_name": errors_map, "warnings_by_name": warnings_map,
        "slowest_runs": slowest, "runs": runs_out,
    }))
}
