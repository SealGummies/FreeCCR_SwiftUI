"""print() must never crash the app.

A macOS .app launched from Finder gets no locale environment, so Python's
stdout/stderr can be ASCII-encoded; a console-less Windows exe can have no
streams at all. Any diagnostic print containing a non-ASCII character (an em
dash in a log line, say) then raises UnicodeEncodeError in the MIDDLE of
whatever operation logged it — on macOS this aborted every B/W-point
conversion (GitHub issue #86). Reconfigure both streams to replace
unencodable characters instead of raising.
"""
import sys


def harden_std_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.reconfigure(errors="backslashreplace")
        except Exception:
            # A wrapped/frozen stream without reconfigure — leave it be; the
            # worst case is the old behaviour for that stream only.
            pass
