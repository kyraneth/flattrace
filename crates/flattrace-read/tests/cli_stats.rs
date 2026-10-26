// SPDX-License-Identifier: MIT
//! Stats parity: the engine report equals `python3 -m flattrace stats --json`
//! over the same files, and the CLI behaves (exit codes, JSON, text).

use serde_json::Value;
use std::path::PathBuf;
use std::process::Command;

fn corpus_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tests/conformance/corpus")
}

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
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
        "first valid line must be run start",
        "run start requires argv, host, version",
        "file contains different runs or tools",
        "missing run end",
        "missing final matching run end",
        "last line must be the matching run end",
        "expected one run start",
        "seq gap or reordering",
        "duplicate span start",
        "unexpected run start",
        "inactive parent",
        "unmatched end",
        "point outside active span",
        "unfinished span",
        "unfinished spans",
    ];
    CATALOG.iter().any(|marker| detail.contains(marker))
}

fn normalize_issues(value: &mut Value) {
    match value {
        Value::Array(items) => {
            for item in items {
                normalize_issues(item);
            }
        }
        Value::Object(map) => {
            if let Some(issues) = map.get_mut("issues").and_then(|v| v.as_array_mut()) {
                for issue in issues.iter_mut() {
                    if let Some(text) = issue.as_str() {
                        let end = text.find(": ").map(|index| index + 2);
                        match end {
                            Some(end)
                                if (text.starts_with("line ") || text.starts_with("incomplete tail "))
                                    && !exact_message(&text[end..]) =>
                            {
                                *issue = Value::String(text[..end].to_string());
                            }
                            _ => {}
                        }
                    }
                }
            }
            for item in map.values_mut() {
                let is_string_list = item
                    .as_array()
                    .map(|a| a.iter().all(|v| v.is_string()))
                    .unwrap_or(false);
                if !is_string_list {
                    normalize_issues(item);
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
            if (a - b).abs() <= tolerance {
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

fn parity_files() -> Vec<String> {
    let mut names: Vec<String> = std::fs::read_dir(corpus_dir().join("parity"))
        .unwrap()
        .filter_map(|entry| entry.ok())
        .map(|entry| entry.path().to_string_lossy().into_owned())
        .collect();
    names.sort();
    names
}

fn run_stats_json(files: &[String]) -> (Value, bool) {
    let output = Command::new("python3")
        .args(["-m", "flattrace", "stats", "--json"])
        .args(files)
        .current_dir(repo_root())
        .output()
        .expect("python3 -m flattrace stats");
    let mut expected: Value = serde_json::from_slice(&output.stdout).unwrap();
    normalize_stats(&mut expected);
    normalize_issues(&mut expected);
    (expected, output.status.success())
}

fn normalize_stats(report: &mut Value) {
        if let Some(runs) = report.get_mut("runs").and_then(|v| v.as_array_mut()) {
            for run in runs.iter_mut() {
                let totals: std::collections::HashMap<String, f64> = run
                    .get("spans")
                    .and_then(|v| v.as_object())
                    .map(|spans| {
                        spans
                            .iter()
                            .map(|(name, summary)| {
                                let total = summary
                                    .get("total_ms")
                                    .and_then(|v| v.get("total"))
                                    .and_then(|v| v.as_f64())
                                    .unwrap_or(0.0);
                                (name.clone(), total)
                            })
                            .collect()
                    })
                    .unwrap_or_default();
                if let Some(dominant) = run.get_mut("dominant_spans").and_then(|v| v.as_array_mut()) {
                    let mut names: Vec<String> = dominant
                        .iter()
                        .filter_map(|v| v.as_str().map(|s| s.to_string()))
                        .collect();
                    names.sort_by(|a, b| {
                        let key = |name: &str| {
                            -((totals.get(name).cloned().unwrap_or(0.0) * 1_000_000.0) as i64)
                        };
                        (key(a), a).cmp(&(key(b), b))
                    });
                    *dominant = names.into_iter().map(Value::String).collect();
                }
            }
        }
    }

#[test]
fn stats_matches_python() {
    let files = corpus_files();
    let expected: Value = {
        let output = Command::new("python3")
            .args(["-m", "flattrace", "stats", "--json"])
            .args(&files)
            .current_dir(repo_root())
            .output()
            .expect("python3 -m flattrace stats");
        // The corpus carries unfinished evidence, so both readers exit 1;
        // exit codes are asserted separately. The JSON report is on stdout.
        serde_json::from_slice(&output.stdout).unwrap()
    };
    let actual = flattrace_read::stats(&files).unwrap();
    // The engine's own dominant_spans must descend by total (ties alphabetical).
    for run in actual.get("runs").and_then(|v| v.as_array()).cloned().unwrap_or_default() {
        let mut last = f64::INFINITY;
        for name in run.get("dominant_spans").and_then(|v| v.as_array()).cloned().unwrap_or_default() {
            let total = name
                .as_str()
                .and_then(|n| {
                    run.get("spans")?.get(n)?.get("total_ms")?.get("total")?.as_f64()
                })
                .unwrap_or(0.0);
            assert!(total <= last + 1e-9, "dominant_spans must descend");
            last = total;
        }
    }
    let mut actual = actual;
    normalize_issues(&mut actual);
    normalize_stats(&mut actual);
    let mut expected = expected;
    normalize_stats(&mut expected);
    normalize_issues(&mut expected);
    approx_equal(&actual, &expected, "stats").unwrap();
}

#[test]
fn stats_matches_python_on_parity_corpus() {
    let files = parity_files();
    let (expected, python_ok) = run_stats_json(&files);
    let mut actual = flattrace_read::stats(&files).unwrap();
    normalize_stats(&mut actual);
    normalize_issues(&mut actual);
    approx_equal(&actual, &expected, "stats parity").unwrap();
    // Unfinished/invalid evidence on both sides: same nonzero exit.
    let binary = env!("CARGO_BIN_EXE_flattrace-read");
    let own = Command::new(binary)
        .arg("stats")
        .args(&files)
        .current_dir(corpus_dir())
        .output()
        .expect("flattrace-read stats parity");
    assert!(!own.status.success());
    assert!(!python_ok);
}

#[test]
fn cli_exit_codes_for_invalid_and_missing_inputs() {
    let binary = env!("CARGO_BIN_EXE_flattrace-read");
    let dir = std::env::temp_dir().join(format!("flattrace-read-cli-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("broken.jsonl"), "{not json\n").unwrap();
    // Invalid-only corpus: digest, stats and check all report validation evidence.
    for args in [
        vec!["digest".to_string(), dir.join("broken.jsonl").to_string_lossy().into_owned()],
        vec!["stats".to_string(), dir.join("broken.jsonl").to_string_lossy().into_owned()],
        vec!["check".to_string(), dir.join("broken.jsonl").to_string_lossy().into_owned()],
    ] {
        let output = Command::new(binary)
            .args(&args)
            .current_dir(&dir)
            .output()
            .expect("flattrace-read invalid corpus");
        assert_eq!(output.status.code(), Some(1), "{args:?}");
    }
    // A missing --events contract is an I/O failure, not a validation issue.
    let missing = Command::new(binary)
        .arg("check")
        .arg(corpus_dir().join("c01-parent.jsonl"))
        .arg("--events")
        .arg(dir.join("no-such-contract.json"))
        .current_dir(&dir)
        .output()
        .expect("flattrace-read missing events");
    assert_eq!(missing.status.code(), Some(2));
    // A directory with no traces at all is an invocation failure.
    std::fs::create_dir_all(dir.join("empty")).unwrap();
    let empty = Command::new(binary)
        .arg("digest")
        .arg(dir.join("empty"))
        .current_dir(&dir)
        .output()
        .expect("flattrace-read empty directory");
    assert_eq!(empty.status.code(), Some(2));
    // A duplicate copy of a broken file still reports validation evidence.
    std::fs::create_dir_all(dir.join("dupbad")).unwrap();
    std::fs::copy(corpus_dir().join("c11-bad.jsonl"), dir.join("dupbad").join("a.jsonl")).unwrap();
    std::fs::copy(corpus_dir().join("c11-bad.jsonl"), dir.join("dupbad").join("b.jsonl")).unwrap();
    let dupbad = Command::new(binary)
        .arg("digest")
        .arg(dir.join("dupbad"))
        .current_dir(&dir)
        .output()
        .expect("flattrace-read duplicate broken corpus");
    assert_eq!(dupbad.status.code(), Some(1));
    let parsed: Value = serde_json::from_slice(&dupbad.stdout).unwrap();
    assert_eq!(parsed["coverage"]["files"], Value::from(2));
    assert!(parsed["coverage"]["duplicates_with_issues"].as_i64().unwrap_or(0) >= 1);
    // Directories expand recursively like `flattrace stats`.
    let expanded = Command::new(binary)
        .arg("digest")
        .arg(corpus_dir().join("closedata"))
        .current_dir(&dir)
        .output()
        .expect("flattrace-read digest directory");
    assert_eq!(expanded.status.code(), Some(0));
    let parsed: Value = serde_json::from_slice(&expanded.stdout).unwrap();
    assert_eq!(parsed["coverage"]["files"], Value::from(1));
    std::fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn stats_python_exit_code_matches() {
    let files = corpus_files();
    let output = Command::new("python3")
        .args(["-m", "flattrace", "stats"])
        .args(&files)
        .current_dir(repo_root())
        .output()
        .expect("python3 -m flattrace stats");
    assert!(!output.status.success());
    let binary = env!("CARGO_BIN_EXE_flattrace-read");
    let own = Command::new(binary)
        .arg("stats")
        .args(&files)
        .current_dir(corpus_dir())
        .output()
        .expect("flattrace-read stats");
    assert_eq!(own.status.code(), output.status.code());
}

#[test]
fn cli_check_digest_tree_group_overlap() {
    let binary = env!("CARGO_BIN_EXE_flattrace-read");
    let files = corpus_files();
    let check = Command::new(binary)
        .arg("check")
        .args(&files)
        .current_dir(corpus_dir())
        .output()
        .expect("flattrace-read check");
    assert_eq!(check.status.code(), Some(1));
    let parsed: Value = serde_json::from_slice(&check.stdout).unwrap();
    assert_eq!(parsed.as_array().unwrap().len(), files.len());
    for (command, extra) in [
        ("digest", Vec::new()),
        ("spans", Vec::new()),
        ("tree", vec!["--trace".to_string(), "a".repeat(32)]),
        ("group", vec!["--by".to_string(), "tool,event".to_string()]),
        ("overlap", vec!["--window".to_string(), "loop.stall".to_string()]),
    ] {
        let output = Command::new(binary)
            .arg(command)
            .args(&extra)
            .args(&files)
            .current_dir(corpus_dir())
            .output()
            .expect("flattrace-read command");
        let expected_code = if command == "spans" { Some(0) } else { Some(1) };
        assert_eq!(output.status.code(), expected_code, "{command}");
        let parsed: Value = serde_json::from_slice(&output.stdout).unwrap();
        if command == "spans" {
            assert!(parsed.as_array().map(|rows| !rows.is_empty()).unwrap_or(false));
        } else {
            assert_eq!(output.status.code(), Some(1), "{command}");
            assert!(parsed.get("coverage").is_some(), "{command}");
        }
        let text = Command::new(binary)
            .arg(command)
            .args(&extra)
            .arg("--text")
            .args(&files)
            .current_dir(corpus_dir())
            .output()
            .expect("flattrace-read text");
        assert_eq!(text.status.code(), expected_code, "{command} text");
        let body = String::from_utf8_lossy(&text.stdout);
        if command == "spans" {
            assert!(body.contains(".jsonl"), "{command} text rows: {body}");
        } else {
            assert!(body.contains("files, "), "{command} text covers: {body}");
        }
    }
    let dir = corpus_dir();
    let compare = Command::new(binary)
        .arg("compare")
        .arg("--a")
        .arg(dir.join("c08-baseline.jsonl"))
        .arg("--b")
        .arg(dir.join("c09-candidate.jsonl"))
        .current_dir(&dir)
        .output()
        .expect("flattrace-read compare");
    assert_eq!(compare.status.code(), Some(0));
    let ok = Command::new(binary)
        .arg("check")
        .arg(dir.join("c01-parent.jsonl"))
        .current_dir(&dir)
        .output()
        .expect("flattrace-read check clean");
    assert_eq!(ok.status.code(), Some(0));
    let bad = Command::new(binary).arg("nope").output().expect("bad command");
    assert_eq!(bad.status.code(), Some(2));
    let runend = Command::new(binary)
        .arg("spans")
        .arg(dir.join("closedata").join("01.jsonl"))
        .current_dir(&dir)
        .output()
        .expect("flattrace-read spans run-end data");
    assert_eq!(runend.status.code(), Some(0));
    let parsed: Value = serde_json::from_slice(&runend.stdout).unwrap();
    let run = parsed
        .as_array()
        .unwrap()
        .iter()
        .find(|row| row["event"] == "run")
        .unwrap()
        .clone();
    assert_eq!(run["end_data"]["turns"], Value::from(3));
}

#[test]
fn help_prints_usage_and_succeeds() {
    for flag in ["--help", "-h", "help"] {
        let out = std::process::Command::new(env!("CARGO_BIN_EXE_flattrace-read"))
            .arg(flag).output().unwrap();
        assert!(out.status.success(), "{flag} exit {:?}", out.status);
        assert!(String::from_utf8_lossy(&out.stdout).starts_with("usage: flattrace-read"));
    }
}
