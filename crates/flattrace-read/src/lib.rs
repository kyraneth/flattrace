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

fn load(paths: &[String]) -> Result<Corpus, EngineError> {
    if paths.is_empty() {
        return Err((2, "no input files".to_string()));
    }
    Corpus::load(paths).map_err(|e| (2, e.to_string()))
}

fn parse_optional(text: Option<&str>) -> Result<Option<Value>, EngineError> {
    match text {
        None => Ok(None),
        Some(text) => serde_json::from_str(text).map(Some).map_err(|e| (2, e.to_string())),
    }
}

pub fn check(paths: &[String], events_path: Option<&str>, command: Option<&str>) -> Result<Value, EngineError> {
    if paths.is_empty() {
        return Err((2, "no input files".to_string()));
    }
    let contract = match events_path {
        Some(path) => Some(validate::read_events(Path::new(path)).map_err(|e| (1, e))?),
        None => None,
    };
    let mut out = Vec::new();
    for path in paths {
        let (rows, mut issues) = validate::read_trace(Path::new(path)).map_err(|e| (2, e.to_string()))?;
        if let Some(contract) = &contract {
            issues.extend(validate::check_events(&rows, contract, command));
        }
        out.push(serde_json::json!({ "file": path, "rows": rows.len(), "issues": issues }));
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
