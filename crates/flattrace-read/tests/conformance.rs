// SPDX-License-Identifier: MIT
//! Golden conformance: the engine reproduces tests/conformance/golden exactly.
//! JSON syntax detail text after `line N:` / `incomplete tail N:` is
//! parser-specific (see docs/reader.md); those details compare by prefix.
//! All other issue strings compare exactly; numbers compare with 1e-9
//! relative tolerance (float summation order may differ in the last ulp).

use serde_json::Value;
use std::path::PathBuf;

fn corpus_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tests/conformance/corpus")
}

fn golden_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tests/conformance/golden")
}

fn corpus_files() -> Vec<String> {
    let mut names: Vec<String> = std::fs::read_dir(corpus_dir())
        .unwrap()
        .filter_map(|entry| entry.ok())
        .map(|entry| entry.path())
        .filter(|path| {
            if !path.is_file() {
                return false;
            }
            let text = path.to_string_lossy().into_owned();
            text.ends_with(".jsonl") || text.ends_with(".jsonl.gz")
        })
        .map(|path| path.to_string_lossy().into_owned())
        .collect();
    names.sort();
    names
}

fn golden(name: &str) -> Value {
    let text = std::fs::read_to_string(golden_dir().join(name)).unwrap();
    serde_json::from_str(&text).unwrap()
}

/// Validation messages that must match exactly; anything else after the
/// `line N:` / `incomplete tail N:` prefix is parser detail text.
fn exact_message(detail: &str) -> bool {
    const CATALOG: &[&str] = &[
        "missing envelope fields",
        "seq must be a nonnegative integer",
        "v must be 1",
        "must be a string",
        "invalid trace",
        "invalid span",
        "invalid parent",
        "run is reserved for lifetime spans",
        "invalid state or data",
        "t must be RFC3339 UTC",
        "end requires took_ms and outcome",
        "invalid took_ms",
        "took_ms and outcome belong on ends",
        "error requires string kind and message",
        "missing run start and end",
        "first line must be run start",
        "run start requires argv, host, version",
        "must be a string",
        "file contains different runs or tools",
        "missing run end",
        "last line must be the matching run end",
        "expected one run start",
        "seq gap or reordering",
        "duplicate span start",
        "inactive parent for",
        "unmatched end",
        "point outside active span",
        "unfinished span",
        "incomplete gzip archive",
        "event list tool mismatch",
        "select a command with --command (or run data.command) for this contract",
        "undeclared command",
        "undeclared event",
        "wrong event kind",
        "missing required event",
    ];
    CATALOG.iter().any(|marker| detail.contains(marker))
}

fn normalize(value: &mut Value) {
    match value {
        Value::Array(items) => {
            for item in items {
                normalize(item);
            }
        }
        Value::Object(map) => {
            for (key, item) in map.iter_mut() {
                if key == "file" {
                    if let Some(path) = item.as_str().map(|s| s.to_string()) {
                        let corpus = corpus_dir().to_string_lossy().into_owned();
                        let prefix = format!("{corpus}/");
                        // Absolute engine paths become corpus-relative like the
                        // goldens; golden relative paths pass through untouched.
                        if let Some(relative) = path.strip_prefix(&prefix) {
                            *item = Value::String(relative.to_string());
                        }
                    }
                } else {
                    normalize(item);
                }
            }
            if map.contains_key("rows") {
                if let Some(issues) = map.get("issues").and_then(|v| v.as_array()).cloned() {
                    let normalized: Vec<Value> = issues
                        .iter()
                        .map(|issue| match issue.as_str() {
                            Some(text) => {
                                let prefix_end = text.find(": ").map(|index| index + 2);
                                match prefix_end {
                                    Some(end)
                                        if (text.starts_with("line ") || text.starts_with("incomplete tail "))
                                            && !exact_message(&text[end..]) =>
                                    {
                                        Value::String(text[..end].to_string())
                                    }
                                    _ => issue.clone(),
                                }
                            }
                            None => issue.clone(),
                        })
                        .collect();
                    map.insert("issues".to_string(), Value::Array(normalized));
                }
            }
        }
        _ => {}
    }
}

fn approx_equal(left: &Value, right: &Value, path: &str) -> Result<(), String> {
    match (left, right) {
        (Value::Number(a), Value::Number(b)) => {
            let (a, b) = (a.as_f64().unwrap_or(f64::NAN), b.as_f64().unwrap_or(f64::NAN));
            let tolerance = 1e-9 * a.abs().max(b.abs()).max(1.0);
            if (a - b).abs() <= tolerance || (a.is_nan() && b.is_nan()) {
                Ok(())
            } else {
                Err(format!("{path}: {a} != {b}"))
            }
        }
        (Value::Array(a), Value::Array(b)) => {
            if a.len() != b.len() {
                return Err(format!("{path}: len {} != {}", a.len(), b.len()));
            }
            for (index, (x, y)) in a.iter().zip(b.iter()).enumerate() {
                approx_equal(x, y, &format!("{path}[{index}]"))?;
            }
            Ok(())
        }
        (Value::Object(a), Value::Object(b)) => {
            if a.len() != b.len() {
                return Err(format!("{path}: keys {a:?} != {b:?}"));
            }
            for (key, x) in a {
                match b.get(key) {
                    Some(y) => approx_equal(x, y, &format!("{path}.{key}"))?,
                    None => return Err(format!("{path}: missing key {key}")),
                }
            }
            Ok(())
        }
        _ => {
            if left == right {
                Ok(())
            } else {
                Err(format!("{path}: {left} != {right}"))
            }
        }
    }
}

fn assert_golden(name: &str, mut actual: Value) {
    let mut expected = golden(name);
    normalize(&mut actual);
    normalize(&mut expected);
    approx_equal(&actual, &expected, name).unwrap();
}

#[test]
fn check_matches_golden() {
    let files = corpus_files();
    let actual = flattrace_read::check(&files, None, None).unwrap();
    assert_golden("check.json", actual);
}

#[test]
fn check_events_matches_golden() {
    let events = golden_dir()
        .parent()
        .unwrap()
        .join("agent-events.json")
        .to_string_lossy()
        .into_owned();
    let actual = flattrace_read::check(
        &[corpus_dir().join("c01-parent.jsonl").to_string_lossy().into_owned()],
        Some(&events),
        None,
    )
    .unwrap();
    assert_golden("check_events.json", actual);
}

#[test]
fn spans_match_golden() {
    let files = corpus_files();
    assert_golden("spans_all.json", flattrace_read::spans(&files, None).unwrap());
    let cases = [
        ("spans_filtered.json", r#"{"event": "tool.exec", "data": {"shot": "sh001"}}"#),
        ("spans_agent.json", r#"{"agent": "synthetic-agent"}"#),
        ("spans_query.json", r#"{"model": "synthetic-model-b", "outcome": "ok"}"#),
        ("spans_session.json", r#"{"session": "session-7"}"#),
        ("spans_errors.json", r#"{"error_kind": "io"}"#),
        (
            "spans_window.json",
            r#"{"t_from": "2026-10-20T00:00:01.000Z", "t_to": "2026-10-20T00:00:01.050Z"}"#,
        ),
    ];
    for (name, filter) in cases {
        assert_golden(name, flattrace_read::spans(&files, Some(filter)).unwrap());
    }
}

#[test]
fn digest_matches_golden() {
    let files = corpus_files();
    let actual = flattrace_read::digest(&files, None).unwrap();
    if let Some(table) = actual.get("table").and_then(|v| v.as_array()) {
        let turn = table
            .iter()
            .find(|entry| {
                entry["tool"] == "agent" && entry["event"] == "agent.turn"
            })
            .unwrap();
        assert!(turn["took"]["p50"].as_i64() == Some(32));
        assert!(turn["self"]["p50"].as_f64() == Some(2.0));
    }
    assert_golden("digest.json", actual);
}

#[test]
fn group_matches_golden() {
    let files = corpus_files();
    assert_golden(
        "group.json",
        flattrace_read::group(&files, &["tool".to_string(), "event".to_string()], None, None).unwrap(),
    );
    assert_golden(
        "group_label.json",
        flattrace_read::group(
            &files,
            &["event".to_string(), "data.shot".to_string()],
            None,
            None,
        )
        .unwrap(),
    );
}

#[test]
fn tree_matches_golden() {
    let files = corpus_files();
    assert_golden("tree.json", flattrace_read::tree(&files, &"a".repeat(32)).unwrap());
}

#[test]
fn compare_matches_golden() {
    let dir = corpus_dir();
    let a = vec![dir.join("c08-baseline.jsonl").to_string_lossy().into_owned()];
    let b = vec![dir.join("c09-candidate.jsonl").to_string_lossy().into_owned()];
    assert_golden("compare.json", flattrace_read::compare(&a, &b, None).unwrap());
}

#[test]
fn overlap_matches_golden() {
    let files = corpus_files();
    assert_golden(
        "overlap.json",
        flattrace_read::overlap(&files, "loop.stall", Some(r#"{"event": "tool.exec"}"#)).unwrap(),
    );
}

#[test]
fn spans_runend_matches_golden() {
    let file = corpus_dir().join("closedata").join("01.jsonl").to_string_lossy().into_owned();
    let actual = flattrace_read::spans(&[file], None).unwrap();
    let run = actual
        .as_array()
        .unwrap()
        .iter()
        .find(|row| row["event"] == "run")
        .unwrap()
        .clone();
    assert_eq!(
        run["end_data"],
        serde_json::json!({ "turns": 3, "tokens": 42, "totals": { "took_ms": 40 } })
    );
    assert_golden("spans_runend.json", actual);
}
