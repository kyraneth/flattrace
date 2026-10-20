# SPDX-License-Identifier: MIT
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from flattrace import Writer
from flattrace.check import read_trace
from flattrace.export import export

ROOT = Path(__file__).resolve().parents[1]


class TraceTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "target").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "target")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / "run.jsonl"
        self.environment = patch.dict(os.environ)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        os.environ.pop("FLATTRACE_TRACEPARENT", None)

    def command(self, *args):
        return subprocess.run([sys.executable, "-m", "flattrace", *map(str, args)],
                              cwd=ROOT, capture_output=True, text=True)

    def test_write_read_and_flush(self):
        writer = Writer(self.path, "oxblend", "dev", argv=["inspect"])
        self.assertEqual(len(self.path.read_text().splitlines()), 1)
        span = writer.start("read", {"path": os.fsdecode(b"/tmp/input-\xff.blend")})
        writer.point("size", {"bytes": 0}, span=span)
        writer.end(span, "refused", error={"kind": "input", "message": "not supported"})
        writer.close("refused")
        rows, issues = read_trace(self.path)
        self.assertFalse(issues)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[1]["data"]["path"], "/tmp/input-\ufffd.blend")
        self.assertEqual(rows[3]["span"], rows[1]["span"])
        self.assertEqual(rows[3]["parent"], rows[0]["span"])
        self.assertGreaterEqual(rows[3]["took_ms"], 0)
        self.assertEqual(rows[-1]["outcome"], "refused")
        self.assertEqual(self.command("check", self.path).returncode, 0)

    def test_killed_writer_has_no_run_end(self):
        code = """from flattrace import Writer
import sys
w = Writer(sys.argv[1], "oxblend", "dev")
w.start("read")
print("ready", flush=True)
sys.stdin.read()
"""
        with subprocess.Popen([sys.executable, "-c", code, str(self.path)], cwd=ROOT,
                              stdout=subprocess.PIPE, stdin=subprocess.PIPE) as proc:
            self.assertEqual(proc.stdout.readline(), b"ready\n")
            proc.kill()
            proc.wait()
        before = self.path.read_bytes()
        rows, issues = read_trace(self.path)
        self.assertEqual(len(rows), 2)
        self.assertTrue(any("unfinished run" in issue for issue in issues))
        result = self.command("check", self.path)
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing run end", result.stdout)
        self.assertEqual(self.path.read_bytes(), before)
        result = self.command("export", self.path, "--out", self.directory / "export")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(read_trace(self.directory / "export/1.jsonl")[0]), 2)

    def test_nested_process_shares_trace(self):
        parent = Writer(self.path, "agent", "dev")
        turn = parent.start("agent.turn")
        child_path = self.directory / "child.jsonl"
        code = """from flattrace import Writer
import sys
w = Writer(sys.argv[1], "oxblend", "dev")
w.point("input", {"path":"scene.blend"})
w.close()
"""
        subprocess.run([sys.executable, "-c", code, str(child_path)], cwd=ROOT,
                       env=parent.child_env(turn), check=True)
        parent.end(turn, "ok")
        parent.close()
        child, issues = read_trace(child_path)
        self.assertFalse(issues)
        self.assertEqual(child[0]["trace"], parent.trace)
        self.assertEqual(child[0]["parent"], turn)
        self.assertNotEqual(child[0]["run"], parent.run)
        self.assertNotEqual(child[0]["span"], turn)

    def test_export_placeholders_and_nothing_else(self):
        source = "/private/project/scene.blend"
        user = getpass.getuser()
        for filename in ("run.jsonl", "child.jsonl"):
            writer = Writer(self.directory / filename, "oxblend", "dev", argv=[source],
                            data={"env": {"host.name": "env.remote.example"}})
            writer.point("details", {
                "path": source, "user": user, "host": "remote.example",
                "note": f"{source} on remote.example by {user}",
                "relative": "scene.blend", "unchanged": [False, None, 7, "payload"],
                "keys": {source: "kept"},
            }, error={"kind": "input", "message": f"Failed to read {source}."})
            writer.close("custom-outcome")
        files = [self.path, self.directory / "child.jsonl"]
        originals = [path.read_bytes() for path in files]
        out = self.directory / "export"
        result = self.command("export", *files, "--out", out,
                              "--host", "remote.example", "--path", "scene.blend", "--path", source)
        self.assertEqual(result.returncode, 0, result.stderr)
        exported = sorted(out.iterdir())
        self.assertEqual([path.name for path in exported], ["1.jsonl", "2.jsonl"])
        for path in exported:
            rows, issues = read_trace(path)
            self.assertFalse(issues)
            data = rows[1]["data"]
            self.assertEqual(data["path"], rows[0]["data"]["argv"][0])
            self.assertEqual(rows[1]["error"]["message"], f"Failed to read {data['path']}.")
            self.assertEqual(data["keys"], {data["path"]: "kept"})
            self.assertEqual(data["unchanged"], [False, None, 7, "payload"])
            self.assertEqual(rows[-1]["outcome"], "custom-outcome")
            text = path.read_text()
            for private in (source, user, "remote.example", "env.remote.example", "scene.blend"):
                self.assertNotIn(private, text)
            self.assertIn("<path-", text)
            self.assertIn("<host-", text)
            self.assertIn("<user-", text)
        self.assertEqual(read_trace(exported[0])[0][1]["data"], read_trace(exported[1])[0][1]["data"])
        self.assertEqual(originals, [path.read_bytes() for path in files])

    def test_check_invalid_lines_and_unknown_duration(self):
        writer = Writer(self.path, "test", "1")
        writer.close("some-tool-outcome")
        valid = [json.loads(line) for line in self.path.read_text().splitlines()]
        valid[-1]["took_ms"] = None
        self.path.write_text("".join(json.dumps(row) + "\n" for row in valid))
        self.assertFalse(read_trace(self.path)[1])
        for update in ({"v": True}, {"state": "oops"}, {"data": []},
                       {"error": {"kind": "input"}}, {"t": "yesterday"}):
            rows = json.loads(json.dumps(valid))
            rows[-1].update(update)
            self.path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            self.assertEqual(self.command("check", self.path).returncode, 1)
        broken = json.loads(json.dumps(valid))
        del broken[0]["data"]["host"]
        self.path.write_text("".join(json.dumps(row) + "\n" for row in broken))
        out = self.directory / "invalid-export"
        result = self.command("export", self.path, "--out", out)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(out.exists())
        self.path.write_text(json.dumps(valid[0]) + "\n" + '{"interrupted":')
        self.assertTrue(any("incomplete tail 2" in issue for issue in read_trace(self.path)[1]))
        self.assertEqual(self.command("export", self.path, "--out", out).returncode, 1)
        self.assertTrue((out / "1.tail.txt").exists())
        self.assertNotIn("interrupted", (out / "1.tail.txt").read_text())
        self.assertEqual(len(read_trace(out / "1.jsonl")[0]), 1)
        split_utf8 = self.directory / "split-utf8.jsonl"
        split_utf8.write_bytes((json.dumps(valid[0]) + "\n").encode() + b'{"text":"\xe2\x82')
        utf8_out = self.directory / "utf8-export"
        self.assertEqual(self.command("export", split_utf8, "--out", utf8_out).returncode, 1)
        self.assertTrue((utf8_out / "1.tail.txt").exists())
        interior = self.directory / "interior.jsonl"
        interior.write_bytes(self.path.read_bytes() + b"\n")
        with self.assertRaises(ValueError):
            export([interior], self.directory / "refused")

    def test_environment_activation_naming_and_sink_failure(self):
        import io
        from contextlib import redirect_stderr
        from flattrace import writer as module
        os.environ.pop("FLATTRACE_DIR", None)
        os.environ["FLATTRACE_TRACEPARENT"] = "bad"
        self.assertIsNone(Writer.from_env("test", "1"))  # off does not inspect context or disk
        os.environ.pop("FLATTRACE_TRACEPARENT")
        os.environ["FLATTRACE_DIR"] = str(self.directory / "traces")
        for _ in range(2):
            w = Writer.from_env("test", "1", data={"task": "frames", "variant": "base"})
            w.close()
            self.assertRegex(w.path.name, r"^\d{8}T\d{6}\.\d{3}Z-test-[0-9a-f]{32}\.jsonl$")
            self.assertTrue(w.path.name.endswith(w.run + ".jsonl"))
            self.assertFalse(read_trace(w.path)[1])
        self.assertEqual(len(list(Path(os.environ["FLATTRACE_DIR"]).glob("*.jsonl"))), 2)
        # Exclusive creation, including a forced collision, never overwrites.
        occupied = Path(os.environ["FLATTRACE_DIR"]) / "occupied.jsonl"
        occupied.write_text("original")
        with patch.object(module, "filename", side_effect=[occupied.name, "fresh.jsonl"]):
            w = Writer.from_env("test", "1")
            w.close()
        self.assertEqual(occupied.read_text(), "original")
        errors = io.StringIO()
        blocked = self.directory / "not-a-directory"
        blocked.write_text("blocked")
        with patch.object(module, "_warned", False), redirect_stderr(errors):
            os.environ["FLATTRACE_DIR"] = str(blocked)
            self.assertIsNone(Writer.from_env("test", "1"))
            self.assertIsNone(Writer.from_env("test", "1"))
        self.assertEqual(errors.getvalue().count("flattrace: tracing disabled"), 1)
        os.environ["FLATTRACE_DIR"] = str(self.directory / "later")
        with patch.object(module, "_warned", False), redirect_stderr(io.StringIO()):
            w = Writer.from_env("test", "1")
            w.file.close()
            w.file = open("/dev/full", "w")  # Linux full-sink reproduction, including as root.
            w.point("operation")
            w.close()
            self.assertIsNone(w.file)

    def test_span_pairing_and_per_command_coverage(self):
        from flattrace.check import read_events, check_events
        contract_path = self.directory / "flattrace-events.json"
        contract_path.write_text(json.dumps({
            "tool": "test", "events": {name: {"kind": "span", "fields": {}}
                                          for name in ("run", "read")},
            "commands": {"summary": ["run", "read"]}}))
        contract = read_events(contract_path)
        w = Writer(self.path, "test", "1", data={"command": "summary"})
        span = w.start("read")
        w.end(span, "ok")
        w.close()
        valid, issues = read_trace(self.path)
        self.assertFalse(issues)
        self.assertFalse(check_events(valid, contract))
        self.assertEqual(self.command("check", self.path, "--events", contract_path).returncode, 0)
        variants = [valid[:1] + valid[2:], valid[:2] + valid[3:],
                    valid[:3] + [valid[2]] + valid[3:], valid[:1] + [valid[2], valid[1]] + valid[3:]]
        for rows in variants:
            self.path.write_text("".join(json.dumps(r) + "\n" for r in rows))
            self.assertEqual(self.command("check", self.path, "--events", contract_path).returncode, 1)
        self.path.write_text("".join(json.dumps(r) + "\n" for r in (valid[0], valid[-1])))
        self.assertEqual(self.command("check", self.path, "--events", contract_path).returncode, 1)
        # A different run cannot satisfy this run's missing operation.
        other = self.directory / "other.jsonl"
        other.write_text("".join(json.dumps(r) + "\n" for r in valid))
        self.assertEqual(self.command("check", self.path, other, "--events", contract_path).returncode, 1)
        rows = json.loads(json.dumps(valid))
        rows[1]["event"] = rows[2]["event"] = "undeclared"
        self.assertTrue(check_events(rows, contract))
        rows[1]["state"] = "point"
        rows[1]["event"] = "read"
        self.assertTrue(any("wrong event kind" in x for x in check_events(rows, contract)))
        valid[0]["future_envelope_field"] = "additive"
        valid[-1]["error"] = {"kind": "context", "message": "example", "future_code": 7}
        self.path.write_text("".join(json.dumps(r) + "\n" for r in valid))
        self.assertFalse(read_trace(self.path)[1])

    def test_async_child_can_outlive_launching_span(self):
        w = Writer(self.path, "agent", "1")
        launch = w.start("tool.call")
        child = w.start("agent.subagent", parent=launch)
        with self.assertRaisesRegex(ValueError, "local spans still active"):
            w.close()
        w.end(launch, "ok")
        w.end(child, "ok")
        w.close()
        self.assertFalse(read_trace(self.path)[1])

    def test_examples_and_learning_join(self):
        complete = sorted((ROOT / "tests/fixtures/synthetic").glob("[1-4].jsonl"))
        self.assertEqual(self.command("check", *complete).returncode, 0)
        summary = ROOT / "examples/oxblend/summary/1.jsonl"
        self.assertEqual(self.command("check", summary, "--events", ROOT / "examples/oxblend/flattrace-events.json").returncode, 0)
        self.assertEqual(self.command("check", ROOT / "tests/fixtures/synthetic/5.jsonl").returncode, 1)
        self.assertEqual(self.command("check", *sorted((ROOT / "tests/fixtures/synthetic").glob("*.jsonl"))).returncode, 1)
        joined = subprocess.run([sys.executable, "examples/learning.py", *map(str, complete)],
                                cwd=ROOT, capture_output=True, text=True, check=True)
        result = json.loads(joined.stdout)
        self.assertEqual(len(result), 1)
        self.assertIs(result[0]["correct"], True)
        self.assertEqual(result[0]["variant"], "candidate")
        self.assertEqual(result[0]["score"], 1)

    def test_review_regressions(self):
        from flattrace.check import read_events, check_events
        w = Writer(self.path, "test", "1", argv=["run", "--token", "test-token", "--password=test-pass", "key=test-key", "---token", "test-triple", "--token="])
        self.assertEqual(read_trace(self.path)[0][0]["data"]["pid"], os.getpid())
        for secret in ("test-token", "test-pass", "test-key", "test-triple"):
            self.assertNotIn(secret, self.path.read_text())
        span = w.start("work")
        w.end(span, "ok")
        w.point("late", {"unknown": None, "notes": "caller note"}, span=span)
        w.point("numbers", {"unknown": None, "nan": float("nan"), "inf": float("inf")})
        w.point("ordinary", {"unknown": None})
        w.point("credentials", {"key": "ctrl-x", "password": "fake-structured", "message": "Authorization: Bearer fake-bearer token=fake-inline https://user:fake-pass@example.test/path"})
        w.close()
        before = self.path.read_bytes()
        w.point("after-close")
        self.assertEqual(self.path.read_bytes(), before)
        rows, issues = read_trace(self.path)
        self.assertFalse(issues)
        self.assertEqual([r["seq"] for r in rows], list(range(len(rows))))
        self.assertEqual(rows[3]["data"]["late_span"], span)
        self.assertEqual(rows[3]["data"]["notes"], ["caller note", "point after span end"])
        self.assertEqual(rows[0]["data"]["argv"][-1], "--token=<redacted>")
        self.assertIsNone(rows[4]["data"]["nan"])
        self.assertIn("notes", rows[4]["data"])
        self.assertNotIn("notes", rows[5]["data"])
        out = self.directory / "credentials-export"
        export([self.path], out)
        text = (out / "1.jsonl").read_text()
        self.assertEqual(read_trace(out / "1.jsonl")[0][6]["data"]["key"], "ctrl-x")
        for secret in ("fake-bearer", "fake-inline", "fake-pass", "fake-structured"):
            self.assertNotIn(secret, text)
        missing = rows[:4] + rows[5:]
        self.path.write_text("".join(json.dumps(r) + "\n" for r in missing))
        self.assertTrue(any("seq gap or reordering" in x for x in read_trace(self.path)[1]))
        self.path.write_bytes(before)
        contract_path = self.directory / "agent-events.json"
        contract_path.write_text(json.dumps({"tool": "test", "required": ["run", "work", "late"],
            "events": {name: {"kind": "span" if name in ("run", "work") else "point", "fields": {}}
                       for name in ("run", "work", "late", "numbers", "ordinary", "credentials")}}))
        self.assertFalse(check_events(rows, read_events(contract_path)))
        self.assertEqual(self.command("check", self.path, "--events", contract_path).returncode, 0)
        complete = sorted((ROOT / "tests/fixtures/synthetic").glob("[1-4].jsonl"))
        from examples.learning import observations
        self.assertEqual(len(observations([*complete, *complete])), 1)

    def test_start_write_cost_is_excluded(self):
        clock = {"value": 0}
        original = Writer._write
        def slow_write(writer, *args, **kwargs):
            original(writer, *args, **kwargs)
            clock["value"] += 10  # Synthetic sink latency, not a real wait.
        with patch("flattrace.writer.time.perf_counter", side_effect=lambda: clock["value"]), patch.object(Writer, "_write", slow_write):
            w = Writer(self.path, "test", "1")
            span = w.start("work")
            clock["value"] += 3  # Synthetic work interval.
            w.end(span, "ok")
            w.close()
        self.assertEqual(read_trace(self.path)[0][2]["took_ms"], 3000)

    @unittest.skipUnless(hasattr(os, "fork"), "fork is a Unix API")
    def test_forked_child_cannot_reuse_writer(self):
        w = Writer(self.path, "test", "1")
        before = self.path.read_bytes()
        pid = os.fork()
        if pid == 0:
            import io
            from contextlib import redirect_stderr
            with redirect_stderr(io.StringIO()):
                w.point("child-must-not-append")
                w.close()
            os._exit(0)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(status, 0)
        self.assertEqual(self.path.read_bytes(), before)
        w.close()
        self.assertFalse(read_trace(self.path)[1])

    def test_earlier_v1_writer_remains_compatible(self):
        legacy = ROOT / "tests/fixtures/legacy/1.jsonl"
        rows, issues = read_trace(legacy)
        self.assertFalse(issues)
        self.assertTrue(all("seq" not in row and row["v"] == 1 for row in rows))
        self.assertNotIn("pid", rows[0]["data"])
        self.assertNotIn("command", rows[0]["data"])
        self.assertEqual(self.command("check", legacy).returncode, 0)
        contract = {"tool": "compat", "required": ["run", "file.open", "bytes"],
                    "events": {name: {"kind": "point" if name == "bytes" else "span", "fields": {}}
                               for name in ("run", "file.open", "bytes")}}
        path = self.directory / "legacy-events.json"
        path.write_text(json.dumps(contract))
        self.assertEqual(self.command("check", legacy, "--events", path).returncode, 0)
        contract["commands"] = {"summary": ["run", "file.open", "bytes"]}
        path.write_text(json.dumps(contract))
        self.assertEqual(self.command("check", legacy, "--events", path, "--command", "summary").returncode, 0)
        self.assertEqual(self.command("check", legacy, "--events", path, "--command", "wrong").returncode, 1)
        out = self.directory / "legacy-export"
        self.assertFalse(export([legacy], out))
        self.assertFalse(read_trace(out / "1.jsonl")[1])

    def test_export_keeps_structure_when_private_names_match_field_names(self):
        for host in ("argv", "run", "summary", "1"):
            path = self.directory / (host + ".jsonl")
            with patch("flattrace.writer.socket.gethostname", return_value=host):
                w = Writer(path, "test", "1", argv=["summary"], data={"command": "summary"})
                w.point("context", {"env": {"host.name": "remote-env.example"}})
                w.close()
            out = self.directory / (host + "-export")
            export([path], out)
            rows, issues = read_trace(out / "1.jsonl")
            self.assertFalse(issues)
            self.assertEqual(rows[0]["data"]["command"], "summary")
            self.assertEqual(rows[0]["tool_version"], "1")
            self.assertIn("<host-", rows[0]["data"]["host"])
        files = [ROOT / "tests/fixtures/synthetic/3.jsonl", ROOT / "tests/fixtures/synthetic/4.jsonl"]
        out = self.directory / "joined-alias-export"
        export(files, out, hosts=["run", "trace", "name", "version", "grader"])
        from examples.learning import observations
        self.assertEqual(len(observations(sorted(out.glob("*.jsonl")))), 1)

    def test_real_oxblend_exports_and_parentage(self):
        base = ROOT / "examples/oxblend"
        for label in ("summary", "trace", "pack", "nested"):
            files = sorted((base / label).glob("*.jsonl"))
            self.assertTrue(files)
            self.assertEqual(self.command("check", *files).returncode, 0)
            for path in files:
                rows, issues = read_trace(path)
                self.assertFalse(issues)
                if rows[0]["tool"] == "oxblend":
                    self.assertEqual(rows[0]["tool_version"], "1.3.0")
                    self.assertEqual(self.command("check", path, "--events", base / "flattrace-events.json").returncode, 0)
        parent, _ = read_trace(base / "nested/1.jsonl")
        child, _ = read_trace(base / "nested/2.jsonl")
        call = next(row for row in parent if row["event"] == "tool.call" and row["state"] == "start")
        self.assertEqual(child[0]["trace"], parent[0]["trace"])
        self.assertEqual(child[0]["parent"], call["span"])
        self.assertNotEqual(child[0]["run"], parent[0]["run"])
        killed, issues = read_trace(base / "killed-pack/1.jsonl")
        self.assertTrue(issues)
        self.assertTrue(all(issue.startswith(("unfinished run ", "unfinished span ")) for issue in issues))
        self.assertFalse(any(row["event"] == "run" and row["state"] == "end" for row in killed))
        self.assertTrue(any(row["event"] == "publish" and row["state"] == "end" and row["data"].get("output_written") for row in killed))
        self.assertEqual(self.command("check", base / "killed-pack/1.jsonl").returncode, 1)

    def test_invalid_context_does_not_create_file(self):
        for value in ("bad", "00-" + "0" * 32 + "-" + "1" * 16 + "-00"):
            os.environ["FLATTRACE_TRACEPARENT"] = value
            with self.assertRaises(ValueError):
                Writer(self.path, "test", "1")
            self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
