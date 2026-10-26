// SPDX-License-Identifier: MIT
//! PyO3 abi3-py310 bindings for the flattrace reader engine.
//! Thin JSON in/out wrappers: every function takes explicit file paths and
//! optional JSON strings and returns a JSON string. Python-side errors map
//! exit-code-1 engine failures to ValueError and the rest to OSError.

use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::prelude::*;

fn engine_error(error: (u8, String)) -> PyErr {
    let (code, message) = error;
    if code == 1 {
        PyValueError::new_err(message)
    } else {
        PyOSError::new_err(message)
    }
}

fn to_json(value: serde_json::Value) -> PyResult<String> {
    serde_json::to_string(&value).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
fn check(paths: Vec<String>, events: Option<String>, command: Option<String>) -> PyResult<String> {
    flattrace_read::check(&paths, events.as_deref(), command.as_deref())
        .map_err(engine_error)
        .and_then(to_json)
}

#[pyfunction]
fn stats(paths: Vec<String>) -> PyResult<String> {
    flattrace_read::stats(&paths).map_err(engine_error).and_then(to_json)
}

#[pyfunction]
fn spans(paths: Vec<String>, filter: Option<String>) -> PyResult<String> {
    flattrace_read::spans(&paths, filter.as_deref()).map_err(engine_error).and_then(to_json)
}

#[pyfunction]
fn digest(paths: Vec<String>, scope: Option<String>) -> PyResult<String> {
    flattrace_read::digest(&paths, scope.as_deref()).map_err(engine_error).and_then(to_json)
}

#[pyfunction]
#[pyo3(signature = (paths, by, metrics=None, filter=None))]
fn group(
    paths: Vec<String>,
    by: Vec<String>,
    metrics: Option<String>,
    filter: Option<String>,
) -> PyResult<String> {
    flattrace_read::group(&paths, &by, metrics.as_deref(), filter.as_deref())
        .map_err(engine_error)
        .and_then(to_json)
}

#[pyfunction]
fn tree(paths: Vec<String>, trace: String) -> PyResult<String> {
    flattrace_read::tree(&paths, &trace).map_err(engine_error).and_then(to_json)
}

#[pyfunction]
fn compare(a: Vec<String>, b: Vec<String>, filter: Option<String>) -> PyResult<String> {
    flattrace_read::compare(&a, &b, filter.as_deref()).map_err(engine_error).and_then(to_json)
}

#[pyfunction]
fn overlap(paths: Vec<String>, window: String, filter: Option<String>) -> PyResult<String> {
    flattrace_read::overlap(&paths, &window, filter.as_deref())
        .map_err(engine_error)
        .and_then(to_json)
}

#[pymodule]
fn _flattrace_read(module: &Bound<PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(check, module)?)?;
    module.add_function(wrap_pyfunction!(stats, module)?)?;
    module.add_function(wrap_pyfunction!(spans, module)?)?;
    module.add_function(wrap_pyfunction!(digest, module)?)?;
    module.add_function(wrap_pyfunction!(group, module)?)?;
    module.add_function(wrap_pyfunction!(tree, module)?)?;
    module.add_function(wrap_pyfunction!(compare, module)?)?;
    module.add_function(wrap_pyfunction!(overlap, module)?)?;
    Ok(())
}
