"""Single shared Pacific-time constant.

Import PACIFIC from here instead of redefining
ZoneInfo("America/Los_Angeles") locally -- consolidated 2026-08-04 after
the same constant was independently copy-pasted into 12+ files (the
same "copy instead of import" habit that caused the driver-identity
name-matcher duplication elsewhere in this codebase)."""
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
