"""System actions: notes in the execution log, explicit approval gates, stop."""

from __future__ import annotations

from pydantic import Field

from app.automation.actions.base import (
    ActionDef,
    ActionEnv,
    ActionInput,
    ActionResult,
    Plan,
    register,
)


class NoteInput(ActionInput):
    note: str = Field(min_length=1, max_length=500)


def _plan_note(_env: ActionEnv, inputs: NoteInput) -> Plan:
    return Plan(
        summary=f"Note: {inputs.note}",
        params=inputs.model_dump(mode="json"),
        expected_effect="Recorded in this execution's timeline only.",
    )


def _execute_note(_env: ActionEnv, inputs: NoteInput) -> ActionResult:
    return ActionResult(summary=f"Note: {inputs.note}", output={"note": inputs.note}, created=False)


register(
    ActionDef(
        type="write_internal_execution_note",
        label="Write execution note",
        category="System",
        description="Record a note in the execution timeline (nothing else changes).",
        input_model=NoteInput,
        risk="LOW",
        permissions=(),
        plan=_plan_note,
        execute=_execute_note,
        template_fields=("note",),
    )
)


class ApprovalGateInput(ActionInput):
    title: str = Field(min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=500)


def _plan_gate(_env: ActionEnv, inputs: ApprovalGateInput) -> Plan:
    return Plan(
        summary=f"Ask for approval: {inputs.title}",
        params=inputs.model_dump(mode="json"),
        expected_effect="The workflow continues only after someone approves.",
        risk="SYSTEM",
    )


def _execute_gate(_env: ActionEnv, inputs: ApprovalGateInput) -> ActionResult:
    return ActionResult(
        summary=f"Approved: {inputs.title}", output={"approved": True}, created=False
    )


register(
    ActionDef(
        type="request_human_approval",
        label="Request approval",
        category="System",
        description="Pause until a team member approves; rejecting stops the workflow.",
        input_model=ApprovalGateInput,
        risk="SYSTEM",
        permissions=(),
        plan=_plan_gate,
        execute=_execute_gate,
        template_fields=("title", "reason"),
        always_requires_approval=True,
    )
)


class StopInput(ActionInput):
    reason: str = Field(default="Stopped by the workflow", min_length=1, max_length=200)


def _plan_stop(_env: ActionEnv, inputs: StopInput) -> Plan:
    return Plan(
        summary=f"Stop: {inputs.reason}",
        params=inputs.model_dump(mode="json"),
        expected_effect="No further steps run.",
        risk="SYSTEM",
    )


def _execute_stop(_env: ActionEnv, inputs: StopInput) -> ActionResult:
    return ActionResult(
        summary=f"Stopped: {inputs.reason}", output={"stopped": True}, created=False
    )


register(
    ActionDef(
        type="stop_workflow",
        label="Stop workflow",
        category="System",
        description="End this run here (as completed).",
        input_model=StopInput,
        risk="SYSTEM",
        permissions=(),
        plan=_plan_stop,
        execute=_execute_stop,
        template_fields=("reason",),
        terminal=True,
    )
)
