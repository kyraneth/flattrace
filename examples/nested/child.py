"""Child tool: joins the caller's trace through FLATTRACE_TRACEPARENT."""
from flattrace import Writer

w = Writer.from_env("my-tool", "1.0", argv=["my-tool", "summary", "scene.blend"])
if w is not None:
    summary = w.start("summary")
    for step in ("file.open", "file.index", "file.schema"):
        w.end(w.start(step, parent=summary), "ok")
    w.end(summary, "ok")
    w.close()
