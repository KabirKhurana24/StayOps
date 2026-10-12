"""Guest-experience actions: requests and in-app messages — through the inbox domain.

Messages are posted to the stay's in-app conversation only. StayOps has no external
delivery provider, so no action ever claims an email/SMS was delivered.
"""

from __future__ import annotations

import uuid
from dataclasses import replace

from pydantic import Field
from sqlalchemy import select

from app.auth.context import Actor
from app.auth.permissions import Permission
from app.automation.actions.base import (
    ActionDef,
    ActionEnv,
    ActionInput,
    ActionResult,
    Plan,
    register,
)
from app.core.errors import DomainValidationError, NotFoundError
from app.guest_requests import service as guest_requests
from app.guest_requests.models import GuestRequest, GuestRequestStatus, GuestRequestType
from app.guest_requests.schemas import GuestRequestCreate
from app.inbox import service as inbox
from app.inbox.models import Conversation, ConversationStatus
from app.reservations.models import Reservation
from app.tasks import service as tasks
from app.tasks.models import TaskPriority


def _conversation_refs(_inputs: object) -> set[str]:
    # The trigger's conversation, or its stay (whose in-app thread is used).
    return {"conversation|reservation"}


def _stay_conversation(env: ActionEnv, *, create: bool) -> tuple[Conversation | None, str]:
    """The trigger's conversation, else the stay's in-app thread (created on execute)."""
    conv_id = env.ref("conversation")
    if conv_id is not None:
        conv = env.session.scalars(
            select(Conversation).where(
                Conversation.id == conv_id, Conversation.organization_id == env.ctx.organization_id
            )
        ).first()
        if conv is None:
            raise NotFoundError("The conversation no longer exists.")
        return conv, "the guest's conversation"
    rid = env.ref("reservation")
    reservation = (
        env.session.scalars(
            select(Reservation).where(
                Reservation.id == rid, Reservation.organization_id == env.ctx.organization_id
            )
        ).first()
        if rid
        else None
    )
    if reservation is None:
        raise DomainValidationError("This workflow's trigger has no guest conversation or stay.")
    if not create:
        existing = env.session.scalars(
            select(Conversation).where(
                Conversation.organization_id == env.ctx.organization_id,
                Conversation.reservation_id == reservation.id,
            )
        ).first()
        return existing, f"the stay {reservation.confirmation_code}"
    conv, _ = inbox.get_or_create_stay_conversation(env.session, env.ctx, reservation)
    return conv, f"the stay {reservation.confirmation_code}"


# -------------------------------------------------------- create_guest_request


class CreateRequestInput(ActionInput):
    type: GuestRequestType = GuestRequestType.OTHER
    priority: TaskPriority = TaskPriority.NORMAL
    description: str = Field(min_length=3, max_length=1000)


def _plan_request(env: ActionEnv, inputs: CreateRequestInput) -> Plan:
    conv, where = _stay_conversation(env, create=False)
    if conv is not None and conv.reservation_id is None:
        raise DomainValidationError("The conversation isn't linked to a stay.")
    return Plan(
        summary=f"Log a {inputs.type.value.replace('_', ' ').lower()} request on {where}",
        params=inputs.model_dump(mode="json"),
        entity_type="conversation" if conv else "reservation",
        entity_id=conv.id if conv else env.ref("reservation"),
        entity_label=where,
        expected_effect=(
            "The request appears in the inbox; the guest sees “request logged” in their thread."
        ),
        risk="MODERATE",
        guest_facing=True,
    )


def _execute_request(env: ActionEnv, inputs: CreateRequestInput) -> ActionResult:
    conv, where = _stay_conversation(env, create=True)
    assert conv is not None  # noqa: S101 — created above
    request_id = guest_requests.create_request(
        env.session,
        env.ctx,
        GuestRequestCreate(
            conversation_id=conv.id,
            type=inputs.type,
            description=inputs.description,
            priority=inputs.priority,
        ),
        client_key=env.key,
    )
    return ActionResult(
        summary=f"Logged a request on {where}",
        output={"guest_request_id": str(request_id), "conversation_id": str(conv.id)},
        entity_type="guest_request",
        entity_id=request_id,
    )


register(
    ActionDef(
        type="create_guest_request",
        label="Log guest request",
        category="Guest experience",
        description="Raise a request on the guest's stay (visible to the guest in their thread).",
        input_model=CreateRequestInput,
        risk="MODERATE",
        guest_facing=True,
        permissions=(Permission.GUEST_REQUESTS_MANAGE, Permission.INBOX_WRITE),
        plan=_plan_request,
        execute=_execute_request,
        refs=_conversation_refs,
        template_fields=("description",),
    )
)


# --------------------------------------------- mark_guest_request_acknowledged


class AcknowledgeInput(ActionInput):
    note: str | None = Field(default=None, max_length=500)
    notify_guest: bool = True


def _subject_request(env: ActionEnv) -> GuestRequest:
    rid = env.ref("guest_request")
    req = (
        env.session.scalars(
            select(GuestRequest).where(
                GuestRequest.id == rid, GuestRequest.organization_id == env.ctx.organization_id
            )
        ).first()
        if rid
        else None
    )
    if req is None:
        raise NotFoundError("The guest request no longer exists.")
    return req


def _plan_ack(env: ActionEnv, inputs: AcknowledgeInput) -> Plan:
    req = _subject_request(env)
    unchanged = req.status != GuestRequestStatus.OPEN
    if req.status in (GuestRequestStatus.CLOSED, GuestRequestStatus.CANCELLED):
        raise DomainValidationError(f"The request is {req.status.value.lower()}.")
    label = guest_requests.label(req.type)
    return Plan(
        summary=f"{label} request is already {req.status.value.lower()}"
        if unchanged
        else f"Acknowledge the {label.lower()} request"
        + (" and tell the guest" if inputs.notify_guest else ""),
        params=inputs.model_dump(mode="json") | {"unchanged": unchanged},
        entity_type="guest_request",
        entity_id=req.id,
        entity_label=f"{label} request",
        expected_effect="No change."
        if unchanged
        else "The guest sees “we've received your request”."
        if inputs.notify_guest
        else "The request is marked acknowledged.",
        snapshot={"status": req.status.value, "version": req.version},
        risk="MODERATE" if inputs.notify_guest else "LOW",
        guest_facing=inputs.notify_guest,
    )


def _execute_ack(env: ActionEnv, inputs: AcknowledgeInput) -> ActionResult:
    plan = _plan_ack(env, inputs)
    req = _subject_request(env)
    if not plan.params["unchanged"]:
        guest_requests.acknowledge_request(
            env.session, env.ctx, req.id, note=inputs.note, notify_guest=inputs.notify_guest
        )
    return ActionResult(
        summary=plan.summary,
        output={"guest_request_id": str(req.id), "notified_guest": inputs.notify_guest},
        entity_type="guest_request",
        entity_id=req.id,
        created=not plan.params["unchanged"],
    )


register(
    ActionDef(
        type="mark_guest_request_acknowledged",
        label="Acknowledge guest request",
        category="Guest experience",
        description="Mark the trigger's request acknowledged (optionally telling the guest).",
        input_model=AcknowledgeInput,
        risk="MODERATE",
        risk_for=lambda i: "MODERATE" if i.notify_guest else "LOW",
        guest_facing=True,
        permissions=(Permission.GUEST_REQUESTS_MANAGE,),
        plan=_plan_ack,
        execute=_execute_ack,
        refs=lambda _i: {"guest_request"},
        template_fields=("note",),
    )
)


# --------------------------------------------------------- send_in_app_message


class MessageInput(ActionInput):
    content: str = Field(min_length=1, max_length=1000)


def _plan_message(env: ActionEnv, inputs: MessageInput) -> Plan:
    conv, where = _stay_conversation(env, create=False)
    if conv is not None and conv.status == ConversationStatus.CLOSED:
        raise DomainValidationError("The conversation is closed.")
    return Plan(
        summary=f"Send an in-app message on {where}",
        params=inputs.model_dump(mode="json"),
        entity_type="conversation" if conv else "reservation",
        entity_id=conv.id if conv else env.ref("reservation"),
        entity_label=where,
        expected_effect="The guest sees the message in their StayOps portal (in-app only — "
        "no email or SMS is sent).",
        risk="MODERATE",
        guest_facing=True,
    )


def _execute_message(env: ActionEnv, inputs: MessageInput) -> ActionResult:
    conv, where = _stay_conversation(env, create=True)
    assert conv is not None  # noqa: S101
    # Guests see the team's name, not the workflow's (the audit keeps the workflow).
    ctx = env.ctx
    if ctx.actor.id is None:
        ctx = replace(ctx, actor=Actor.system(inbox.SYSTEM_LABEL))
    message_id = inbox.send_message(env.session, ctx, conv.id, inputs.content, client_key=env.key)
    return ActionResult(
        summary=f"Posted an in-app message on {where}",
        output={
            "message_id": str(message_id),
            "conversation_id": str(conv.id),
            "delivery": "in_app",
            "external_delivery": False,
        },
        entity_type="conversation",
        entity_id=conv.id,
    )


register(
    ActionDef(
        type="send_in_app_message",
        label="Send in-app message",
        category="Guest experience",
        description="Post a message to the guest's in-app thread (no external delivery).",
        input_model=MessageInput,
        risk="MODERATE",
        guest_facing=True,
        permissions=(Permission.INBOX_WRITE,),
        plan=_plan_message,
        execute=_execute_message,
        refs=_conversation_refs,
        template_fields=("content",),
    )
)


# -------------------------------------------------------- assign_conversation


class AssignConversationInput(ActionInput):
    assignee_id: uuid.UUID


def _plan_assign_conv(env: ActionEnv, inputs: AssignConversationInput) -> Plan:
    conv, where = _stay_conversation(env, create=False)
    if conv is None:
        raise DomainValidationError("The stay has no conversation yet.")
    assignee = tasks.member_name(tasks.assert_assignable(env.session, env.ctx, inputs.assignee_id))
    return Plan(
        summary=f"Assign {where} to {assignee}",
        params=inputs.model_dump(mode="json") | {"conversation_id": str(conv.id)},
        entity_type="conversation",
        entity_id=conv.id,
        entity_label=where,
        expected_effect=f"{assignee} owns the conversation in the inbox.",
        snapshot={
            "version": conv.version,
            "assigned_to": str(conv.assigned_to) if conv.assigned_to else None,
        },
        risk="MODERATE",
    )


def _execute_assign_conv(env: ActionEnv, inputs: AssignConversationInput) -> ActionResult:
    plan = _plan_assign_conv(env, inputs)
    conv_id = uuid.UUID(plan.params["conversation_id"])
    inbox.assign_conversation(env.session, env.ctx, conv_id, inputs.assignee_id)
    return ActionResult(
        summary=plan.summary,
        output={"conversation_id": str(conv_id), "assignee_id": str(inputs.assignee_id)},
        entity_type="conversation",
        entity_id=conv_id,
    )


register(
    ActionDef(
        type="assign_conversation",
        label="Assign conversation",
        category="Guest experience",
        description="Route the guest's conversation to a team member.",
        input_model=AssignConversationInput,
        risk="MODERATE",
        permissions=(Permission.INBOX_WRITE,),
        plan=_plan_assign_conv,
        execute=_execute_assign_conv,
        refs=_conversation_refs,
        member_fields=("assignee_id",),
    )
)
