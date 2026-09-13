"""Streaming sinks for the REPL.

``utils.llm_client`` renders streamed model output through a *sink*. The
default sink (:class:`utils.llm_client.PrintSink`) preserves the original
one-shot ``print()`` behavior; the REPL injects :class:`ReplSink`, which frames
each turn so streamed output never runs into the next prompt.

Sinks are deliberately tiny and I/O-only: they carry no agent or policy logic,
so one-shot ``run`` output is unchanged when no sink is supplied.
"""

from __future__ import annotations

from utils.llm_client import PrintSink, StreamSink

__all__ = ["StreamSink", "PrintSink", "ReplSink", "BufferSink"]

# Printed once before each streamed turn to keep turns visually distinct.
TURN_SEPARATOR = "\n"


class ReplSink(PrintSink):
    """Prints the stream with a separator so consecutive turns stay distinct.

    Inherits the exact one-shot framing from :class:`PrintSink`; the only
    difference is the extra separator emitted once per stream (i.e. per turn).
    """

    def __init__(self, separator: str = TURN_SEPARATOR):
        super().__init__()
        self._separator = separator

    def start(self) -> None:
        if self._separator:
            print(self._separator, end="", flush=True)
        super().start()


class BufferSink:
    """Captures streamed text instead of printing it (tests / piped output)."""

    def __init__(self):
        self.reasoning_text = ""
        self.content_text = ""
        self.started = False
        self.ended = False

    def start(self) -> None:
        self.reasoning_text = ""
        self.content_text = ""
        self.started = True
        self.ended = False

    def reasoning(self, text: str) -> None:
        self.reasoning_text += text

    def content(self, text: str) -> None:
        self.content_text += text

    def end(self) -> None:
        self.ended = True
