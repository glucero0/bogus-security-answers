"""Run a callback on the Tk main thread.

Worker threads must not call Tk. The main window drains a queue from its own
event loop, and background work posts callbacks onto that queue.
"""

from __future__ import annotations


def invoke_on_main(widget, callback) -> None:
    root = widget.winfo_toplevel()
    schedule = getattr(root, "call_on_main", None)
    if callable(schedule):
        schedule(callback)
        return
    widget.after(0, callback)
