// SPDX-License-Identifier: MIT
//! Corpus statistics with the exact flattrace.stats report shape.
//! Aggregates read typed in-memory records; only exact-quantile samples
//! spill to the temporary SQLite file, like flattrace.stats.

use crate::pair::{render_issues, Corpus};
use serde_json::{json, Value};
use std::collections::BTreeMap;

pub fn stats(corpus: &Corpus) -> Result<Value, String> {
    let store = &corpus.store;
    let io_err = |e: std::io::Error| e.to_string();
    store.begin().map_err(io_err)?;
    let mut runs_out = Vec::new();
    let mut took_list: Vec<Value> = Vec::new();
    for (position, run) in corpus.runs.iter().enumerate().filter(|(_, r)| !r.duplicate) {
        took_list.push(run.took.clone());
        let mut names: Vec<String> = Vec::new();
        let mut seen = std::collections::HashSet::new();
        for name in run.unknown.keys().chain(run.unfinished.keys()) {
            if seen.insert(name.clone()) {
                names.push(name.clone());
            }
        }
        for &index in &corpus.spans_by_run[position] {
            let event = corpus.spans[index].event.clone();
            if seen.insert(event.clone()) {
                names.push(event);
            }
        }
        names.sort();
        let mut spans_map = serde_json::Map::new();
        let mut totals: BTreeMap<String, f64> = BTreeMap::new();
        for name in &names {
            let total_sum = store.summary(position, "took", name).map_err(io_err)?;
            let self_sum = store.summary(position, "self", name).map_err(io_err)?;
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
        let mut issue_counts = serde_json::Map::new();
        let mut issue_keys: Vec<&String> = run.issue_counts.keys().collect();
        issue_keys.sort();
        for key in issue_keys {
            issue_counts.insert(key.clone(), json!(run.issue_counts[key]));
        }
        let mut unfinished = serde_json::Map::new();
        let mut unfinished_keys: Vec<&String> = run.unfinished.keys().collect();
        unfinished_keys.sort();
        for key in unfinished_keys {
            unfinished.insert(key.clone(), json!(run.unfinished[key]));
        }
        let mut unknown = serde_json::Map::new();
        let mut unknown_names: Vec<&String> = run.unknown.keys().collect();
        unknown_names.sort();
        for name in unknown_names {
            let mut reasons = serde_json::Map::new();
            let mut reason_keys: Vec<&String> = run.unknown[name].keys().collect();
            reason_keys.sort();
            for reason in reason_keys {
                reasons.insert(reason.clone(), json!(run.unknown[name][reason]));
            }
            unknown.insert(name.clone(), Value::Object(reasons));
        }
        runs_out.push(json!({
            "path": run.path, "trace": run.trace, "run": run.run, "tool": run.tool,
            "tool_version": run.tool_version, "status": run.status, "took_ms": run.took,
            "level": run.level, "events": run.events,
            "issues": run.issues_rendered,
            "issue_counts": issue_counts,
            "unfinished_spans": unfinished,
            "self_unknown": unknown,
            "spans": spans_map, "dominant_spans": dominant,
        }));
    }
    store.commit().map_err(io_err)?;
    let mut duplicates_out = Vec::new();
    for run in corpus.runs.iter().filter(|r| r.duplicate) {
        let mut issue_counts = serde_json::Map::new();
        let mut issue_keys: Vec<&String> = run.issue_counts.keys().collect();
        issue_keys.sort();
        for key in issue_keys {
            issue_counts.insert(key.clone(), json!(run.issue_counts[key]));
        }
        duplicates_out.push(json!({
            "path": run.path, "trace": run.trace, "run": run.run, "status": run.status,
            "issues": run.issues_rendered, "issue_counts": issue_counts,
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
    let tmp = store.tmp_id();
    store.begin().map_err(io_err)?;
    let mut all_names: Vec<String> = Vec::new();
    {
        let mut seen = std::collections::HashSet::new();
        for run in corpus.runs.iter().filter(|r| !r.duplicate) {
            for name in run.unknown.keys().chain(run.unfinished.keys()) {
                if seen.insert(name.clone()) {
                    all_names.push(name.clone());
                }
            }
        }
        for span in &corpus.spans {
            if corpus.runs.get(span.run_idx).map(|r| r.duplicate).unwrap_or(true) {
                continue;
            }
            if seen.insert(span.event.clone()) {
                all_names.push(span.event.clone());
            }
            store.insert_tmp(tmp, "took", &span.event, &span.took).map_err(io_err)?;
            if !span.self_ms.is_null() {
                store.insert_tmp(tmp, "self", &span.event, &span.self_ms).map_err(io_err)?;
            }
        }
    }
    all_names.sort();
    let mut spans_out = serde_json::Map::new();
    for name in &all_names {
        let total_sum = store.tmp_summary(tmp, "took", name).map_err(io_err)?;
        let self_sum = store.tmp_summary(tmp, "self", name).map_err(io_err)?;
        spans_out.insert(name.clone(), json!({
            "total_ms": total_sum.map(|s| s.json_float()).unwrap_or(Value::Null),
            "self_ms": self_sum.map(|s| s.json_float()).unwrap_or(Value::Null),
        }));
    }
    store.clear_tmp().map_err(io_err)?;
    store.commit().map_err(io_err)?;
    let mut statuses: BTreeMap<String, i64> = BTreeMap::new();
    let mut levels: BTreeMap<String, i64> = BTreeMap::new();
    for run in corpus.runs.iter().filter(|r| !r.duplicate) {
        *statuses.entry(run.status.clone()).or_insert(0) += 1;
        *levels.entry(run.level.clone()).or_insert(0) += 1;
    }
    let mut slowest: Vec<usize> = (0..runs_out.len()).collect();
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
