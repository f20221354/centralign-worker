"""The model's three jobs: understand the goal, propose the next action, read a document. It executes nothing."""
from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import ValidationError

from src.agent import client, prompts
from src.agent.tools import openai_tools
from src.contracts import GoalSpec, TaskState, to_paise


class Agent(Protocol):
    def understand(self, goal_text: str) -> GoalSpec: ...
    def propose(self, state: TaskState) -> dict[str, Any] | None: ...
    def extract(self, text: str) -> dict[str, Any]: ...


class LLMAgent:
    def __init__(self) -> None:
        self.tools = openai_tools()
        self.last_provider = ""

    def understand(self, goal_text: str) -> GoalSpec:
        raw = client.complete_json(prompts.GOAL_PROMPT.format(goal=goal_text))
        thr = raw.pop("approval_threshold_inr", None)
        raw = {k: v for k, v in raw.items() if k in GoalSpec.model_fields}
        try:
            raw["approval_threshold_paise"] = to_paise(thr) if thr not in (None, "", 0) else None
        except ValueError:
            raw["approval_threshold_paise"] = None
        try:
            return GoalSpec.model_validate(raw)
        except ValidationError as e:  # one repair round, then fail loudly
            fixed = client.complete_json(prompts.GOAL_PROMPT.format(goal=goal_text) +
                                         f"\n\nYour previous answer was invalid: {e.errors()[:3]}. Fix it.")
            fixed.pop("approval_threshold_inr", None)
            return GoalSpec.model_validate({k: v for k, v in fixed.items() if k in GoalSpec.model_fields})

    def propose(self, state: TaskState) -> dict[str, Any] | None:
        """-> {"tool_name", "arguments", "action_rationale"} (unvalidated), or None if the model made no call."""
        msgs = [{"role": "system", "content": prompts.SYSTEM},
                {"role": "user", "content": prompts.state_message(state)}]
        for _ in range(2):
            msg, self.last_provider = client.complete(msgs, self.tools)
            calls = msg.tool_calls or []
            if calls:
                fn = calls[0].function
                try:
                    args = json.loads(fn.arguments or "{}")
                except json.JSONDecodeError:
                    args = {"_unparseable_arguments": (fn.arguments or "")[:200]}
                if not isinstance(args, dict):
                    args = {"_unparseable_arguments": str(args)[:200]}
                rationale = str(args.pop("action_rationale", ""))[:300]
                return {"tool_name": fn.name, "arguments": args, "action_rationale": rationale}
            msgs += [{"role": "assistant", "content": msg.content or ""},
                     {"role": "user", "content": "Call exactly one tool now (use finish if the goal is done)."}]
        return None

    def extract(self, text: str) -> dict[str, Any]:
        return client.complete_json(prompts.EXTRACT_PROMPT.format(text=text[:6000]))
