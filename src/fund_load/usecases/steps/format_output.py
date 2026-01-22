from __future__ import annotations

import json

from fund_load.usecases.messages import Decision, OutputLine


class FormatOutput:
    # Step 07 formats Decision into JSON with deterministic key order (docs/implementation/steps/07 FormatOutput.md).
    def __call__(self, msg: Decision, ctx: object | None) -> list[OutputLine]:
        # Only id, customer_id, accepted are emitted; internal fields are ignored.
        payload = {"id": msg.id, "customer_id": msg.customer_id, "accepted": msg.accepted}
        json_text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        return [OutputLine(line_no=msg.line_no, json_text=json_text)]
