"""A rule-based investigator that speaks the same protocol as a model.

It plays two roles: CI exercises the whole loop with no API key, and it is the
floor any model has to beat in the evals. Its rule is the one a tired on-call
engineer reaches for first: blame the last deploy before the errors began.
"""

import json
from datetime import datetime
from itertools import count
from typing import Any

from anthropic.types.beta import BetaMessage

from rootcause.application.budget import Price
from rootcause.application.investigator import SUBMIT_DIAGNOSIS
from rootcause.domain.scenario import Alert, Category

PRICES = {"heuristic": Price(0, 0, 0, 0)}
_ids = count(1)


def _message(content: list[dict[str, Any]]) -> BetaMessage:
    return BetaMessage.model_validate(
        {
            "id": f"msg_heuristic_{next(_ids)}",
            "type": "message",
            "role": "assistant",
            "model": "heuristic",
            "content": content,
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
    )


def _call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": f"toolu_h{next(_ids)}", "name": name, "input": args}


def _results_by_tool(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Tool name -> parsed JSON result, across every tool_result so far."""
    names: dict[str, str] = {}
    out: dict[str, Any] = {}
    for msg in messages:
        if not isinstance(msg["content"], list):
            continue
        for block in msg["content"]:
            if block.get("type") == "tool_use":
                names[block["id"]] = block["name"]
            elif block.get("type") == "tool_result" and not block.get("is_error"):
                try:
                    out[names[block["tool_use_id"]]] = json.loads(block["content"])
                except json.JSONDecodeError, KeyError, TypeError:
                    continue
    return out


class HeuristicProvider:
    name = "heuristic"

    async def complete(
        self,
        *,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> BetaMessage:
        alert = Alert.parse(messages[0]["content"])
        service = alert.service if alert else None
        seen = _results_by_tool(messages)

        if "query_logs" not in seen:
            return _message(
                [
                    _call("query_logs", {"service": service, "level": "error", "limit": 20}),
                    _call("list_deploys", {}),
                ]
            )

        errors = seen["query_logs"]
        if not errors and "query_metrics" not in seen:
            return _message([_call("query_metrics", {"service": service})])
        onset = min((datetime.fromisoformat(e["ts"]) for e in errors), default=None)
        suspects = [
            d
            for d in seen.get("list_deploys", [])
            if onset and datetime.fromisoformat(d["ts"]) <= onset
        ]
        suspect = suspects[-1] if suspects else None

        if suspect and "diff_config" not in seen:
            return _message([_call("diff_config", {"deploy_id": suspect["id"]})])

        evidence = [e["id"] for e in errors[:1]]
        if suspect:
            changes = seen.get("diff_config", [])
            category = Category.CONFIG_CHANGE if changes else Category.BAD_CODE_DEPLOY
            evidence = [suspect["id"], *[c["id"] for c in changes[:1]], *evidence]
            summary = f"Errors began after deploy {suspect['id']} of {suspect['service']}."
            action, blamed = f"Roll back {suspect['id']}.", suspect["service"]
        elif errors:
            category, summary = Category.DEPENDENCY_FAILURE, "No deploy precedes the errors."
            action, blamed = "Page the owner of the failing dependency.", service
        else:
            category, summary = Category.NO_INCIDENT, "No errors found for the alerted service."
            action, blamed = "Tune the alert.", service
            evidence = [m["id"] for m in seen.get("query_metrics", [])[:1]]

        diagnosis = {
            "category": category,
            "service": blamed or "unknown",
            "summary": summary,
            "evidence": evidence,
            "recommended_action": action,
            "confidence": 0.5,
        }
        return _message([_call(SUBMIT_DIAGNOSIS.name, diagnosis)])
