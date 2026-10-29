// SPDX-License-Identifier: MIT
//! flattrace-read: offline reader engine for flattrace v1 JSONL.
//! Streaming corpus load, SQLite disk spill, exact nearest-rank summaries.
//! See docs/reader.md for the frozen function contract.

pub mod pair;
pub mod query;
pub mod stats;
pub mod store;
pub mod validate;

use serde_json::Value;
use std::path::Path;

pub use pair::Corpus;

/// Engine error with the process exit code the CLI must use: 1 means the
/// input carries validation or unfinished evidence (like `flattrace check`
/// and `flattrace stats`); 2 means invocation or I/O failure.
pub type EngineError = (u8, String);

fn walk_sorted(dir: &Path, out: &mut Vec<String>) -> Result<(), String> {
    let fail = |path: &Path, e: std::io::Error| format!("{}: {e}", path.display());
    let mut entries: Vec<std::fs::DirEntry> = std::fs::read_dir(dir)
        .map_err(|e| fail(dir, e))?
        .collect::<Result<_, _>>()
        .map_err(|e| fail(dir, e))?;
    entries.sort_by_key(|e| e.file_name());
    for entry in entries {
        let path = entry.path();
        let file_type = entry.file_type().map_err(|e| fail(&path, e))?;
        let is_link =
            std::fs::symlink_metadata(&path).map(|m| m.file_type().is_symlink()).unwrap_or(false);
        if file_type.is_dir() && !is_link {
            walk_sorted(&path, out)?;
        } else {
            let name = path.to_string_lossy();
            if name.ends_with(".jsonl") || name.ends_with(".jsonl.gz") {
                out.push(name.into_owned());
            }
        }
    }
    Ok(())
}

/// Accept explicit files and directories, mirroring
/// `flattrace.stats.trace_files`: recursive, sorted, never following dir
/// links. An empty expansion is an invocation failure.
fn expand_inputs(paths: &[String]) -> Result<Vec<String>, EngineError> {
    if paths.is_empty() {
        return Err((2, "no input files".to_string()));
    }
    let mut out = Vec::new();
    for path in paths {
        let fs_path = Path::new(path);
        match std::fs::symlink_metadata(fs_path) {
            Err(e) => return Err((2, format!("{path}: {e}"))),
            Ok(meta) if meta.file_type().is_dir() && !meta.file_type().is_symlink() => {
                walk_sorted(fs_path, &mut out).map_err(|e| (2, e))?;
            }
            Ok(_) => out.push(path.clone()),
        }
    }
    if out.is_empty() {
        return Err((2, "no .jsonl or .jsonl.gz trace files found".to_string()));
    }
    Ok(out)
}

fn load(paths: &[String]) -> Result<Corpus, EngineError> {
    let paths = expand_inputs(paths)?;
    Corpus::load(&paths).map_err(|e| (2, e.to_string()))
}

fn parse_optional(text: Option<&str>) -> Result<Option<Value>, EngineError> {
    match text {
        None => Ok(None),
        Some(text) => serde_json::from_str(text).map(Some).map_err(|e| (2, e.to_string())),
    }
}

pub fn check(paths: &[String], events: &[String], command: Option<&str>) -> Result<Value, EngineError> {
    let paths = expand_inputs(paths)?;
    let mut contracts = Vec::new();
    for path in events {
        contracts.push(validate::read_events(Path::new(path)).map_err(|e| match e {
            validate::ReadEventsError::Io(inner) => (2, inner.to_string()),
            validate::ReadEventsError::Invalid(msg) => (1, msg),
        })?);
    }
    let mut out = Vec::new();
    for path in &paths {
        let (rows, mut issues, mut notices) = validate::read_trace(Path::new(path)).map_err(|e| (2, e.to_string()))?;
        if !contracts.is_empty() {
            let (extra, extra_notices) = validate::check_events(&rows, &contracts, command);
            issues.extend(extra);
            notices.extend(extra_notices);
        }
        out.push(serde_json::json!({ "file": path, "rows": rows.len(), "issues": issues, "notices": notices }));
    }
    Ok(Value::Array(out))
}

pub fn stats(paths: &[String]) -> Result<Value, EngineError> {
    stats::stats(&load(paths)?).map_err(|e| (2, e))
}

pub fn spans(paths: &[String], filter: Option<&str>) -> Result<Value, EngineError> {
    let corpus = load(paths)?;
    let filt = parse_optional(filter)?;
    query::spans(&corpus, filt.as_ref()).map_err(|e| (2, e))
}

pub fn digest(paths: &[String], scope: Option<&str>) -> Result<Value, EngineError> {
    let corpus = load(paths)?;
    let filt = parse_optional(scope)?;
    query::digest(&corpus, filt.as_ref()).map_err(|e| (2, e))
}

pub fn group(paths: &[String], by: &[String], metrics: Option<&str>, filter: Option<&str>) -> Result<Value, EngineError> {
    let corpus = load(paths)?;
    let filt = parse_optional(filter)?;
    let metrics_value = parse_optional(metrics)?;
    query::group(&corpus, by, metrics_value.as_ref(), filt.as_ref()).map_err(|e| (2, e))
}

pub fn tree(paths: &[String], trace: &str) -> Result<Value, EngineError> {
    query::tree(&load(paths)?, trace).map_err(|e| (2, e))
}

pub fn compare(a: &[String], b: &[String], filter: Option<&str>) -> Result<Value, EngineError> {
    let left = load(a)?;
    let right = load(b)?;
    let filt = parse_optional(filter)?;
    query::compare(&left, &right, filt.as_ref()).map_err(|e| (2, e))
}

pub fn overlap(paths: &[String], window: &str, filter: Option<&str>) -> Result<Value, EngineError> {
    let corpus = load(paths)?;
    let filt = parse_optional(filter)?;
    query::overlap(&corpus, window, filt.as_ref()).map_err(|e| (2, e))
}
