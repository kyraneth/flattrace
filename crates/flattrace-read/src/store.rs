// SPDX-License-Identifier: MIT
//! Disk-backed spill for exact quantiles only, mirroring flattrace.stats:
//! one SQLite table of samples, everything else lives in typed Rust structs
//! in memory. No memory cap and no size threshold; the temporary database
//! file is removed on drop.

use rusqlite::{params, Connection};
use serde_json::Value;
use std::io;

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
            "CREATE TABLE sample (kind TEXT, gkey TEXT, seqno INTEGER,
               vint INTEGER, is_int INTEGER, vreal REAL);
             CREATE INDEX sample_key ON sample(kind, gkey);",
        )
        .map_err(|e| io::Error::new(io::ErrorKind::Other, e.to_string()))?;
        Ok(Store { conn, path, seqno: std::cell::Cell::new(0), tmp_count: std::cell::Cell::new(0) })
    }

    fn exec(&self, sql: &str, p: &[&dyn rusqlite::ToSql]) -> io::Result<usize> {
        self.conn.prepare_cached(sql).and_then(|mut s| s.execute(p))
            .map_err(|e| io::Error::new(io::ErrorKind::Other, e.to_string()))
    }

    /// Batch a whole corpus load or query spill in one transaction, the way
    /// flattrace.stats holds a single SQLite transaction per analyse call.
    pub fn begin(&self) -> io::Result<()> {
        self.conn.execute_batch("BEGIN")
            .map_err(|e| io::Error::new(io::ErrorKind::Other, e.to_string()))
    }

    pub fn commit(&self) -> io::Result<()> {
        self.conn.execute_batch("COMMIT")
            .map_err(|e| io::Error::new(io::ErrorKind::Other, e.to_string()))
    }

    pub fn tmp_id(&self) -> u64 {
        let id = self.tmp_count.get();
        self.tmp_count.set(id + 1);
        id
    }

    pub fn insert_sample(&self, run_idx: usize, kind: &str, gkey: &str, value: &Value) -> io::Result<()> {
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

    pub fn delete_samples(&self, kind: &str) -> io::Result<()> {
        self.exec("DELETE FROM sample WHERE kind=?1", params![kind])?;
        Ok(())
    }

    pub fn clear_tmp(&self) -> io::Result<()> {
        self.exec("DELETE FROM sample WHERE kind LIKE 'tmp\u{1f}%'", &[])?;
        Ok(())
    }

    pub fn summary(&self, run_idx: usize, kind: &str, gkey: &str) -> io::Result<Option<Summary>> {
        Summary::query(&self.conn, &format!("{run_idx}\u{1f}{kind}"), gkey)
    }

    pub fn tmp_summary(&self, id: u64, kind: &str, gkey: &str) -> io::Result<Option<Summary>> {
        Summary::query(&self.conn, &format!("tmp\u{1f}{id}\u{1f}{kind}"), gkey)
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
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
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
                    |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
                )
                .map_err(err)?;
            values.push(Self::number(vint, is_int, vreal));
        }
        let max: (Option<i64>, i64, f64) = conn
            .query_row(
                "SELECT vint, is_int, vreal FROM sample WHERE kind=?1 AND gkey=?2
                 ORDER BY vreal DESC, seqno ASC LIMIT 1",
                params![kind, gkey],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
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
