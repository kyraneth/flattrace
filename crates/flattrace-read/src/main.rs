// SPDX-License-Identifier: MIT
//! flattrace-read CLI: check/stats/digest/spans/tree/compare/overlap/group.
//! JSON by default; --text prints a compact human form including the coverage
//! statement. Exit 0 is clean, 1 is validation/unfinished evidence, 2 is
//! invocation or I/O failure.

use std::process::ExitCode;

fn usage() -> String {
    "usage: flattrace-read check FILES... [--events FILE] [--command ID]\n       flattrace-read stats FILES... [--json|--text]\n       flattrace-read digest FILES... [--filter JSON] [--json|--text]\n       flattrace-read spans FILES... [--filter JSON] [--json|--text]\n       flattrace-read tree --trace ID FILES... [--json|--text]\n       flattrace-read compare --a A [--a ...] --b B [--b ...] [--filter JSON] [--json|--text]\n       flattrace-read overlap --window EVENT FILES... [--filter JSON] [--json|--text]\n       flattrace-read group --by a,b [--metrics m,n] FILES... [--filter JSON] [--json|--text]".to_string()
}

struct Args {
    command: String,
    files: Vec<String>,
    a_files: Vec<String>,
    b_files: Vec<String>,
    events: Option<String>,
    command_id: Option<String>,
    filter: Option<String>,
    by: Vec<String>,
    metrics: Option<String>,
    trace: Option<String>,
    window: Option<String>,
    text: bool,
}

fn parse(argv: &[String]) -> Result<Args, String> {
    let mut args = Args {
        command: String::new(), files: Vec::new(), a_files: Vec::new(), b_files: Vec::new(),
        events: None, command_id: None, filter: None, by: Vec::new(),
        metrics: None, trace: None, window: None, text: false,
    };
    let mut iter = argv.iter();
    match iter.next() {
        Some(command) => args.command = command.clone(),
        None => return Err(usage()),
    }
    let mut side: u8 = 0;
    let mut pending: Option<String> = None;
    let mut tokens: Vec<String> = iter.cloned().collect();
    tokens.reverse();
    while let Some(token) = tokens.pop() {
        if let Some(flag) = pending.take() {
            match flag.as_str() {
                "--events" => args.events = Some(token),
                "--command" => args.command_id = Some(token),
                "--filter" => args.filter = Some(token),
                "--metrics" => args.metrics = Some(token),
                "--trace" => args.trace = Some(token),
                "--window" => args.window = Some(token),
                "--by" => args.by = token.split(',').map(|s| s.to_string()).collect(),
                _ => return Err(format!("unknown flag {flag}\n{}", usage())),
            }
            continue;
        }
        match token.as_str() {
            "--events" | "--command" | "--filter" | "--by" | "--metrics" | "--trace" | "--window" => {
                pending = Some(token);
            }
            "--text" => args.text = true,
            "--json" => args.text = false,
            "--a" => side = 1,
            "--b" => side = 2,
            other if other.starts_with("--") => return Err(format!("unknown flag {other}\n{}", usage())),
            file => match side {
                1 => args.a_files.push(file.to_string()),
                2 => args.b_files.push(file.to_string()),
                _ => args.files.push(file.to_string()),
            },
        }
    }
    if pending.is_some() {
        return Err(format!("missing value for {}\n{}", pending.unwrap(), usage()));
    }
    Ok(args)
}

fn main() -> ExitCode {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let args = match parse(&argv) {
        Ok(args) => args,
        Err(message) => {
            eprintln!("{message}");
            return ExitCode::from(2);
        }
    };
    match run(&args) {
        Ok(code) => ExitCode::from(code),
        Err((code, message)) => {
            eprintln!("flattrace-read: {message}");
            ExitCode::from(code)
        }
    }
}

fn summarize(value: &serde_json::Value) -> String {
    if value.is_null() {
        return "n=0 p50=? p95=? max=? total=?".to_string();
    }
    ["n", "p50", "p95", "max", "total"].iter()
        .map(|key| format!("{}={}", key, trim_number(&value[key])))
        .collect::<Vec<_>>().join(" ")
}

fn trim_number(value: &serde_json::Value) -> String {
    match value {
        serde_json::Value::Number(n) => n.to_string(),
        serde_json::Value::String(s) => s.clone(),
        serde_json::Value::Null => "?".to_string(),
        _ => value.to_string(),
    }
}

fn coverage_of(value: &serde_json::Value) -> String {
    value.get("coverage").and_then(|c| c.get("statement"))
        .and_then(|s| s.as_str()).unwrap_or("?").to_string()
}

fn text_for(command: &str, value: &serde_json::Value) -> String {
    let mut lines = Vec::new();
    match command {
        "check" => {
            for file in value.as_array().cloned().unwrap_or_default() {
                let issues = file.get("issues").and_then(|v| v.as_array()).cloned().unwrap_or_default();
                if issues.is_empty() {
                    lines.push(format!("{}: {} valid events", file["file"].as_str().unwrap_or("?"), file["rows"].as_i64().unwrap_or(0)));
                } else {
                    lines.push(format!("{}: {}", file["file"].as_str().unwrap_or("?"),
                        issues.iter().filter_map(|v| v.as_str()).collect::<Vec<_>>().join("; ")));
                }
            }
        }
        "stats" => {
            lines.push(format!("Runs: {}  files: {}  duplicates excluded: {}",
                value["run_count"].as_i64().unwrap_or(0), value["files"].as_i64().unwrap_or(0),
                value["duplicates"].as_array().map(|d| d.len()).unwrap_or(0)));
            lines.push(format!("Status: {}", value["statuses"]));
            if let Some(spans) = value.get("spans").and_then(|v| v.as_object()) {
                for (name, summary) in spans {
                    lines.push(format!("  {name}: {}", summarize(&summary["total_ms"])));
                    lines.push(format!("    self: {}", summarize(&summary["self_ms"])));
                }
            }
        }
        "digest" => {
            lines.push(coverage_of(value));
            if let Some(table) = value.get("table").and_then(|v| v.as_array()) {
                for entry in table {
                    lines.push(format!("  {} {} n={} took {} slowest {}",
                        entry["tool"].as_str().unwrap_or("?"), entry["event"].as_str().unwrap_or("?"),
                        entry["n"].as_i64().unwrap_or(0), summarize(&entry["took"]), entry["slowest"]));
                }
            }
            if let Some(errors) = value.get("errors").and_then(|v| v.as_array()) {
                for error in errors {
                    lines.push(format!("  error {} {} records={} occurrences={}",
                        error["event"].as_str().unwrap_or("?"),
                        error["kind"].as_str().unwrap_or("?"),
                        error["records"].as_i64().unwrap_or(0), error["occurrences"].as_i64().unwrap_or(0)));
                }
            }
        }
        "spans" => {
            if let Some(rows) = value.as_array() {
                lines.push(format!("{} spans", rows.len()));
                for row in rows {
                    lines.push(format!("  {} {} took={} {}",
                        row["event"].as_str().unwrap_or("?"), row["span"].as_str().unwrap_or("?"),
                        trim_number(&row["took_ms"]), row["file"].as_str().unwrap_or("?")));
                }
            }
        }
        "group" => {
            lines.push(coverage_of(value));
            if let Some(groups) = value.get("groups").and_then(|v| v.as_array()) {
                for group in groups {
                    lines.push(format!("  {} n={} took {}", group["key"],
                        group["n"].as_i64().unwrap_or(0), summarize(&group["took"])));
                }
            }
        }
        "tree" => {
            lines.push(coverage_of(value));
            lines.push(format!("trace {} slowest={} failed={}",
                value["trace"].as_str().unwrap_or("?"), value["slowest"], value["failed"]));
            fn walk(node: &serde_json::Value, depth: usize, lines: &mut Vec<String>) {
                lines.push(format!("{}- {} {} {} {}", "  ".repeat(depth),
                    node["run"].as_str().unwrap_or("?"), node["status"].as_str().unwrap_or("?"),
                    trim_number(&node["took_ms"]), node["file"].as_str().unwrap_or("?")));
                if let Some(children) = node.get("children").and_then(|v| v.as_array()) {
                    for child in children {
                        walk(child, depth + 1, lines);
                    }
                }
            }
            if let Some(roots) = value.get("roots").and_then(|v| v.as_array()) {
                for root in roots {
                    walk(root, 1, &mut lines);
                }
            }
        }
        "compare" => {
            if let Some(events) = value.get("events").and_then(|v| v.as_array()) {
                for entry in events {
                    lines.push(format!("  {} {} a={} b={} d_n={} d_p50={} d_total={}",
                        entry["tool"].as_str().unwrap_or("?"), entry["event"].as_str().unwrap_or("?"),
                        summarize(&entry["a"]["took"]), summarize(&entry["b"]["took"]),
                        trim_number(&entry["delta_n"]), trim_number(&entry["delta_p50"]),
                        trim_number(&entry["delta_total"])));
                }
            }
        }
        "overlap" => {
            lines.push(coverage_of(value));
            if let Some(windows) = value.get("windows").and_then(|v| v.as_array()) {
                for window in windows {
                    let hits = window.get("overlapping").and_then(|v| v.as_array()).map(|h| h.len()).unwrap_or(0);
                    lines.push(format!("  window {} hits={hits}", window["window"]));
                }
            }
        }
        _ => lines.push(value.to_string()),
    }
    lines.join("\n")
}

fn run(args: &Args) -> Result<u8, (u8, String)> {
    let value = match args.command.as_str() {
        "check" => {
            if args.files.is_empty() {
                return Err((2, "check needs input files".to_string()));
            }
            flattrace_read::check(&args.files, args.events.as_deref(), args.command_id.as_deref())?
        }
        "stats" => {
            if args.files.is_empty() {
                return Err((2, "stats needs input files".to_string()));
            }
            flattrace_read::stats(&args.files)?
        }
        "digest" => flattrace_read::digest(&args.files, args.filter.as_deref())?,
        "spans" => flattrace_read::spans(&args.files, args.filter.as_deref())?,
        "group" => {
            if args.by.is_empty() {
                return Err((2, "group needs --by".to_string()));
            }
            flattrace_read::group(&args.files, &args.by, args.metrics.as_deref(), args.filter.as_deref())?
        }
        "tree" => {
            let trace = args.trace.clone().ok_or_else(|| (2, "tree needs --trace".to_string()))?;
            flattrace_read::tree(&args.files, &trace)?
        }
        "compare" => {
            if args.a_files.is_empty() || args.b_files.is_empty() {
                return Err((2, "compare needs --a and --b".to_string()));
            }
            flattrace_read::compare(&args.a_files, &args.b_files, args.filter.as_deref())?
        }
        "overlap" => {
            let window = args.window.clone().ok_or_else(|| (2, "overlap needs --window".to_string()))?;
            flattrace_read::overlap(&args.files, &window, args.filter.as_deref())?
        }
        other => return Err((2, format!("unknown command {other}\n{}", usage()))),
    };
    if args.text {
        println!("{}", text_for(&args.command, &value));
    } else {
        println!("{}", serde_json::to_string_pretty(&value).unwrap_or_default());
    }
    Ok(exit_code(&args.command, &value))
}

fn has_issues(command: &str, value: &serde_json::Value) -> bool {
    match command {
        "check" => value.as_array().map(|files| files.iter().any(|f| {
            f.get("issues").and_then(|v| v.as_array()).map(|i| !i.is_empty()).unwrap_or(false)
        })).unwrap_or(false),
        "spans" => false,
        "compare" => ["a", "b"].iter().any(|side| {
            value.get(side).and_then(|v| v.get("coverage")).and_then(|c| c.get("issues"))
                .and_then(|v| v.as_i64()).unwrap_or(0) > 0
        }),
        "stats" => {
            let runs = value.get("runs").and_then(|v| v.as_array()).map(|r| r.len()).unwrap_or(0);
            let invalid = value.get("invalid_files").and_then(|v| v.as_array()).map(|r| r.len()).unwrap_or(0);
            let duplicates: usize = value.get("duplicates").and_then(|v| v.as_array())
                .map(|d| d.iter().filter(|r| {
                    r.get("issues").and_then(|v| v.as_array()).map(|i| !i.is_empty()).unwrap_or(false)
                }).count()).unwrap_or(0);
            let run_issues = value.get("runs").and_then(|v| v.as_array())
                .map(|r| r.iter().any(|run| {
                    run.get("issues").and_then(|v| v.as_array()).map(|i| !i.is_empty()).unwrap_or(false)
                })).unwrap_or(false);
            invalid > 0 || duplicates > 0 || run_issues || runs == 0
        }
        _ => value.get("coverage").and_then(|c| c.get("issues"))
            .and_then(|v| v.as_i64()).unwrap_or(0) > 0,
    }
}

fn exit_code(command: &str, value: &serde_json::Value) -> u8 {
    if has_issues(command, value) { 1 } else { 0 }
}
