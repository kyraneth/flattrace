// SPDX-License-Identifier: MIT
//! Disk-backed spill for the reader engine: span records and exact-quantile
//! samples live in a temporary SQLite file, never in process memory.
//! Ordering ties break by insertion order, matching Python's stable sort.

use rusqlite::{params, Connection};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::io;

pub struct SpanInsert<'a> {
    pub run_idx: i64,
    pub file: &'a str,
    pub span: &'a str,
    pub parent_json: &'a Value,
    pub event: &'a str,
    pub tool: &'a str,
    pub tool_version: &'a str,
    pub trace: &'a str,
    pub run: &'a str,
    pub start_t: &'a str,
    pub start_seq: &'a Value,
    pub end_t: Option<Value>,
    pub end_seq: Option<Value>,
    pub took: &'a Value,
    pub took_num: Option<f64>,
    pub outcome: &'a Value,
    pub unfinished: bool,
    pub depth: i64,
    pub children: &'a [String],
    pub data: &'a Value,
    pub end_data: Option<Value>,
    pub error: &'a Value,
    pub self_ms: &'a Value,
    pub self_unknown: &'a Value,
    pub points: &'a [Value],
}

pub struct Store {
    conn: Connection,
    path: std::path::PathBuf,
    seqno: std::cell::Cell<i64>,
    tmp_count: std::cell::Cell<u64>,
}

impl Store {
    pub fn open() -> io::Result<Store> {
        let mut path = std::env::temp_dir();
        path.push(format!("flattrace-read-{}-{}.sqlite", std::process::id(), store_counter()));
        let conn = Connection::open(&path)
            .map_err(|e| io::Error::new(io::ErrorKind::Other, e.to_string()))?;
        conn.execute_batch(
            "CREATE TABLE run (idx INTEGER PRIMARY KEY, path TEXT, trace TEXT, run TEXT,
               tool TEXT, tool_version TEXT, status TEXT, took TEXT, agent TEXT,
               header TEXT, level TEXT, unfinished TEXT, unknown_json TEXT,
               issues_rendered TEXT, issue_counts TEXT, events INTEGER,
               duplicate INTEGER);
             CREATE TABLE span (id INTEGER PRIMARY KEY, run_idx INTEGER, file TEXT,
               span TEXT, parent_json TEXT, event TEXT, tool TEXT, tool_version TEXT,
               trace TEXT, run TEXT, start_t TEXT, start_seq TEXT, end_t TEXT,
               end_seq TEXT, took TEXT, took_num REAL, outcome TEXT, unfinished INTEGER,
               depth INTEGER, children TEXT, data TEXT, end_data TEXT, error TEXT,
               self_ms TEXT, self_unknown TEXT, points TEXT);
             CREATE TABLE sample (kind TEXT, gkey TEXT, seqno INTEGER,
               vint INTEGER, is_int INTEGER, vreal REAL);
             CREATE INDEX sample_key ON sample(kind, gkey);",
        )
        .map_err(|e| io::Error::new(io::ErrorKind::Other, e.to_string()))?;
        Ok(Store { conn, path, seqno: std::cell::Cell::new(0), tmp_count: std::cell::Cell::new(0) })
    }

    fn exec(&self, sql: &str, p: &[&dyn rusqlite::ToSql]) -> io::Result<usize> {
        self.conn.execute(sql, p).map_err(|e| io::Error::new(io::ErrorKind::Other, e.to_string()))
    }

    /// Batch a whole corpus load or query spill in one transaction, the way
    /// flattrace.stats holds a single SQLite transaction per analyse call.
    /// Samples and rows still spill to the temporary database file.
    pub fn begin(&self) -> io::Result<()> {
        self.conn.execute_batch("BEGIN")
            .map_err(|e| io::Error::new(io::ErrorKind::Other, e.to_string()))
    }

    pub fn commit(&self) -> io::Result<()> {
        self.conn.execute_batch("COMMIT")
            .map_err(|e| io::Error::new(io::ErrorKind::Other, e.to_string()))
    }

    pub fn insert_run(&self, path: &str, trace: &str, run: &str, tool: &str,
        tool_version: &str, agent: Option<&str>, header: &Value, level: &str, duplicate: bool) -> io::Result<i64> {
        self.exec(
            "INSERT INTO run (path, trace, run, tool, tool_version, status, took, agent,
              header, level, unfinished, unknown_json, issues_rendered, issue_counts, events, duplicate)
             VALUES (?1, ?2, ?3, ?4, ?5, 'unfinished', 'null', ?6, ?7, ?8, '{}', '{}', '[]', '{}', 0, ?9)",
            params![path, trace, run, tool, tool_version,
                agent.map(|s| s.to_string()),
                header.to_string(), level, duplicate as i64],
        )?;
        Ok(self.conn.last_insert_rowid())
    }

    pub fn finish_run(&self, idx: i64, status: &str, took: &Value, unknown: &Value,
        unfinished: &Value, rendered: &[String], counts: &BTreeMap<String, i64>, events: i64) -> io::Result<()> {
        let rendered_json: Vec<Value> = rendered.iter().map(|s| Value::String(s.clone())).collect();
        let mut counts_map = serde_json::Map::new();
        for (category, count) in counts {
            counts_map.insert(category.clone(), json!(*count));
        }
        self.exec(
            "UPDATE run SET status=?1, took=?2, unknown_json=?3, unfinished=?4,
             issues_rendered=?5, issue_counts=?6, events=?7 WHERE idx=?8",
            params![status, took.to_string(), unknown.to_string(), unfinished.to_string(),
                Value::Array(rendered_json).to_string(), Value::Object(counts_map).to_string(),
                events, idx],
        )?;
        Ok(())
    }

    #[allow(clippy::too_many_arguments)]
    pub fn insert_span(&self, s: SpanInsert) -> io::Result<()> {
        let children: Vec<Value> = s.children.iter().map(|c| Value::String(c.clone())).collect();
        let points = Value::Array(s.points.to_vec()).to_string();
        self.exec(
            "INSERT INTO span (run_idx, file, span, parent_json, event, tool, tool_version,
              trace, run, start_t, start_seq, end_t, end_seq, took, took_num, outcome,
              unfinished, depth, children, data, end_data, error, self_ms, self_unknown, points)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15,
                     ?16, ?17, ?18, ?19, ?20, ?21, ?22, ?23, ?24, ?25)",
            params![s.run_idx, s.file, s.span, s.parent_json.to_string(), s.event,
                s.tool, s.tool_version, s.trace, s.run, s.start_t, s.start_seq.to_string(),
                s.end_t.map(|v| v.to_string()), s.end_seq.map(|v| v.to_string()),
                s.took.to_string(), s.took_num,
                s.outcome.to_string(), s.unfinished as i64, s.depth,
                Value::Array(children).to_string(), s.data.to_string(),
                s.end_data.map(|v| v.to_string()), s.error.to_string(),
                s.self_ms.to_string(), s.self_unknown.to_string(), points],
        )?;
        Ok(())
    }

    pub fn tmp_id(&self) -> u64 {
        let id = self.tmp_count.get();
        self.tmp_count.set(id + 1);
        id
    }

    pub fn insert_sample(&self, run_idx: i64, kind: &str, gkey: &str, value: &Value) -> io::Result<()> {
        self.insert_sample_raw(&format!("{run_idx}\u{1f}{kind}"), gkey, value)
    }

    pub fn insert_tmp(&self, id: u64, kind: &str, gkey: &str, value: &Value) -> io::Result<()> {
        self.insert_sample_raw(&format!("tmp\u{1f}{id}\u{1f}{kind}"), gkey, value)
    }

    pub fn insert_sample_raw(&self, kind: &str, gkey: &str, value: &Value) -> io::Result<()> {
        let (vint, is_int, vreal) = match value {
            Value::Number(n) if n.is_i64() || n.is_u64() => {
                let v = n.as_i64().unwrap_or(i64::MAX);
                (Some(v), 1, v as f64)
            }
            Value::Number(n) => (None, 0, n.as_f64().unwrap_or(f64::NAN)),
            _ => return Ok(()),
        };
        let seqno = self.seqno.get();
        self.seqno.set(seqno + 1);
        self.exec(
            "INSERT INTO sample (kind, gkey, seqno, vint, is_int, vreal) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![kind, gkey, seqno, vint, is_int, vreal],
        )?;
        Ok(())
    }

    pub fn clear_tmp(&self) -> io::Result<()> {
        self.exec("DELETE FROM sample WHERE kind LIKE 'tmp\u{1f}%'", &[])?;
        Ok(())
    }

    pub fn tmp_summary(&self, id: u64, kind: &str, gkey: &str) -> io::Result<Option<Summary>> {
        Summary::query(&self.conn, &format!("tmp\u{1f}{id}\u{1f}{kind}"), gkey)
    }

    pub fn nuke_self(&self, run_idx: i64) -> io::Result<()> {
        self.exec("DELETE FROM sample WHERE kind=?1",
            params![format!("{run_idx}\u{1f}self")])?;
        self.exec("UPDATE span SET self_ms='null', self_unknown='\"incomplete or invalid run\"'
                   WHERE run_idx=?1 AND self_ms IS NOT NULL AND self_ms != 'null'",
            params![run_idx])?;
        Ok(())
    }

    pub fn spans_with_self(&self, run_idx: i64) -> io::Result<Vec<(String, i64)>> {
        let mut stmt = self.conn.prepare(
            "SELECT event, COUNT(*) FROM span WHERE run_idx=?1 AND self_ms != 'null' GROUP BY event")
            .map_err(|e| io::Error::new(io::ErrorKind::Other, e.to_string()))?;
        let rows = stmt.query_map(params![run_idx],
            |row: &rusqlite::Row| Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?)))
            .map_err(|e| io::Error::new(io::ErrorKind::Other, e.to_string()))?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| io::Error::new(io::ErrorKind::Other, e.to_string()))
    }

    pub fn primary_runs(&self) -> io::Result<Vec<i64>> {
        let mut stmt = self.conn.prepare("SELECT idx FROM run ORDER BY idx")
            .map_err(|e| io::Error::new(io::ErrorKind::Other, e.to_string()))?;
        let rows = stmt.query_map([], |row: &rusqlite::Row| row.get::<_, i64>(0))
            .map_err(|e| io::Error::new(io::ErrorKind::Other, e.to_string()))?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| io::Error::new(io::ErrorKind::Other, e.to_string()))
    }

    pub fn summary(&self, run_idx: i64, kind: &str, gkey: &str) -> io::Result<Option<Summary>> {
        Summary::query(&self.conn, &format!("{run_idx}\u{1f}{kind}"), gkey)
    }

    pub fn connection(&self) -> &Connection {
        &self.conn
    }
}

impl Drop for Store {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
    }
}

fn store_counter() -> u64 {
    use std::sync::atomic::{AtomicU64, Ordering};
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    COUNTER.fetch_add(1, Ordering::Relaxed)
}

pub struct Summary {
    pub n: i64,
    pub values: Vec<Value>,
    pub total: Value,
}

impl Summary {
    fn number(vint: Option<i64>, is_int: i64, vreal: f64) -> Value {
        if is_int == 1 {
            Value::Number(vint.unwrap_or(0).into())
        } else {
            serde_json::Number::from_f64(vreal).map(Value::Number).unwrap_or(Value::Null)
        }
    }

    pub fn query(conn: &Connection, kind: &str, gkey: &str) -> io::Result<Option<Summary>> {
        let err = |e: rusqlite::Error| io::Error::new(io::ErrorKind::Other, e.to_string());
        let (n, int_sum, real_sum, int_count): (i64, Option<i64>, Option<f64>, Option<i64>) = conn
            .query_row(
                "SELECT COUNT(*), SUM(CASE WHEN is_int=1 THEN vint END),
                        SUM(CASE WHEN is_int=0 THEN vreal END),
                        SUM(CASE WHEN is_int=1 THEN 1 ELSE 0 END)
                 FROM sample WHERE kind=?1 AND gkey=?2",
                params![kind, gkey],
                |row: &rusqlite::Row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
            )
            .map_err(err)?;
        if n == 0 {
            return Ok(None);
        }
        let int_count = int_count.unwrap_or(0);
        let total = if int_count == n {
            Value::Number(int_sum.unwrap_or(0).into())
        } else {
            let total = int_sum.unwrap_or(0) as f64 + real_sum.unwrap_or(0.0);
            serde_json::Number::from_f64(total).map(Value::Number).unwrap_or(Value::Null)
        };
        let mut values = Vec::new();
        for want in [0.5, 0.95] {
            let offset = (n as f64 * want).ceil() as i64 - 1;
            let (vint, is_int, vreal): (Option<i64>, i64, f64) = conn
                .query_row(
                    "SELECT vint, is_int, vreal FROM sample WHERE kind=?1 AND gkey=?2
                     ORDER BY vreal ASC, seqno ASC LIMIT 1 OFFSET ?3",
                    params![kind, gkey, offset],
                    |row: &rusqlite::Row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
                )
                .map_err(err)?;
            values.push(Self::number(vint, is_int, vreal));
        }
        let max: (Option<i64>, i64, f64) = conn
            .query_row(
                "SELECT vint, is_int, vreal FROM sample WHERE kind=?1 AND gkey=?2
                 ORDER BY vreal DESC, seqno ASC LIMIT 1",
                params![kind, gkey],
                |row: &rusqlite::Row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            )
            .map_err(err)?;
        values.push(Self::number(max.0, max.1, max.2));
        Ok(Some(Summary { n, values, total }))
    }

    pub fn json(&self) -> Value {
        serde_json::json!({
            "n": self.n,
            "p50": self.values[0],
            "p95": self.values[1],
            "max": self.values[2],
            "total": self.total,
        })
    }

    /// stats.py shapes pass through a SQLite REAL column, so every sample
    /// reads back float: mirror that coercion for the stats report only.
    pub fn json_float(&self) -> Value {
        let coerce = |value: &Value| match value {
            Value::Number(n) => serde_json::Number::from_f64(n.as_f64().unwrap_or(f64::NAN))
                .map(Value::Number)
                .unwrap_or(Value::Null),
            _ => value.clone(),
        };
        serde_json::json!({
            "n": self.n,
            "p50": coerce(&self.values[0]),
            "p95": coerce(&self.values[1]),
            "max": coerce(&self.values[2]),
            "total": coerce(&self.total),
        })
    }
}
