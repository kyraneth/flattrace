// SPDX-License-Identifier: MIT
//! napi-rs bindings for the flattrace reader engine.
//! Thin JSON in/out wrappers, mirroring the PyO3 module: explicit file
//! paths in, JSON strings out. The Node loader parses and names load
//! failures EngineUnavailable with the platform.

use napi::bindgen_prelude::*;
use napi_derive::napi;

fn engine_error(error: (u8, String)) -> napi::Error {
    napi::Error::new(Status::GenericFailure, error.1)
}

fn to_json(value: serde_json::Value) -> napi::Result<String> {
    serde_json::to_string(&value)
        .map_err(|e| napi::Error::new(Status::GenericFailure, format!("{e}")))
}

#[napi]
pub fn check(paths: Vec<String>, events: Option<String>, command: Option<String>) -> napi::Result<String> {
    flattrace_read::check(&paths, events.as_deref(), command.as_deref())
        .map_err(engine_error)
        .and_then(to_json)
}

#[napi]
pub fn stats(paths: Vec<String>) -> napi::Result<String> {
    flattrace_read::stats(&paths).map_err(engine_error).and_then(to_json)
}

#[napi]
pub fn spans(paths: Vec<String>, filter: Option<String>) -> napi::Result<String> {
    flattrace_read::spans(&paths, filter.as_deref()).map_err(engine_error).and_then(to_json)
}

#[napi]
pub fn digest(paths: Vec<String>, scope: Option<String>) -> napi::Result<String> {
    flattrace_read::digest(&paths, scope.as_deref()).map_err(engine_error).and_then(to_json)
}

#[napi]
pub fn group(
    paths: Vec<String>,
    by: Vec<String>,
    metrics: Option<String>,
    filter: Option<String>,
) -> napi::Result<String> {
    flattrace_read::group(&paths, &by, metrics.as_deref(), filter.as_deref())
        .map_err(engine_error)
        .and_then(to_json)
}

#[napi]
pub fn tree(paths: Vec<String>, trace: String) -> napi::Result<String> {
    flattrace_read::tree(&paths, &trace).map_err(engine_error).and_then(to_json)
}

#[napi]
pub fn compare(a: Vec<String>, b: Vec<String>, filter: Option<String>) -> napi::Result<String> {
    flattrace_read::compare(&a, &b, filter.as_deref()).map_err(engine_error).and_then(to_json)
}

#[napi]
pub fn overlap(paths: Vec<String>, window: String, filter: Option<String>) -> napi::Result<String> {
    flattrace_read::overlap(&paths, &window, filter.as_deref())
        .map_err(engine_error)
        .and_then(to_json)
}
