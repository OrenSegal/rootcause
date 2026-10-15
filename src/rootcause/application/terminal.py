"""Terminal tools: the call an agent makes to hand in its answer and end its run.

The tool's input schema is generated from the answer's pydantic model, so the
model is the one definition of the contract. Strict tool use accepts a subset
of JSON Schema, so numeric and length bounds are dropped from the schema the
API sees and enforced by pydantic when the call arrives instead.
"""

from typing import Any

from pydantic import BaseModel, ValidationError

_DROP = {
    "title", "default", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minLength", "maxLength", "minItems", "maxItems", "pattern",
}  # fmt: skip


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].rsplit("/", 1)[-1]])
            out = {k: walk(v) for k, v in node.items() if k not in _DROP}
            if out.get("type") == "object":
                out["additionalProperties"] = False
                out["required"] = sorted(out.get("properties", {}))
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(raw)


class TerminalTool[A: BaseModel]:
    def __init__(self, name: str, answer: type[A]) -> None:
        self.name = name
        self.answer = answer
        self.definition: dict[str, Any] = {
            "name": name,
            "description": (answer.__doc__ or "").strip(),
            "strict": True,
            "input_schema": strict_schema(answer),
        }

    def parse(self, args: dict[str, Any]) -> A | str:
        """The answer, or an error message the model can act on."""
        try:
            return self.answer.model_validate(args)
        except ValidationError as e:
            return f"Invalid {self.name} input, fix and resubmit: {e}"
