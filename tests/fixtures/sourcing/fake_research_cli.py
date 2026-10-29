"""Stand-in for `claude -p --output-format json --json-schema ...` in sourcing tests.

Prints a Claude Code JSON result whose ``structured_output`` is the research
fixture named by ``$FAKE_RESEARCH_FIXTURE``; records argv and stdin to
``$FAKE_RESEARCH_LOG``. ``FAKE_RESEARCH_MODE=invalid`` returns schema-violating output.
"""

import json
import os
import sys
from pathlib import Path

prompt = sys.stdin.read()
log = os.environ.get("FAKE_RESEARCH_LOG")
if log:
    Path(log).write_text(json.dumps({"argv": sys.argv[1:], "stdin": prompt}))
payload = json.loads(Path(os.environ["FAKE_RESEARCH_FIXTURE"]).read_text())
payload.pop("_note", None)
if os.environ.get("FAKE_RESEARCH_MODE") == "invalid":
    payload = {"candidates": [{"name": "missing fields"}]}
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "", "structured_output": payload,
                  "total_cost_usd": 0.0, "num_turns": 1}))
