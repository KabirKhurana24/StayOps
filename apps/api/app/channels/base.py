"""Channel adapters: the boundary between the inbox and messaging providers.

Every channel (in-app, SMS, WhatsApp, Airbnb, …) implements the same interface.
The inbox core never imports a provider SDK; it asks the registry for the
conversation's adapter and gets an honest status back. Only the in-app channel is
connected today — the others report ``NOT_CONFIGURED`` and refuse to send, so the
product never claims a message reached a guest when it didn't.

Inbound flow (future providers): webhook → ``verify_webhook`` → ``normalize_event``
→ ``InboundMessage`` → ``receive_message`` → inbox service (idempotency key derived
from the provider's message id, e.g. ``integration:twilio:SM123``).
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.core.errors import AppError
from app.inbox.models import Channel


class ChannelStatus(StrEnum):
    CONNECTED = "CONNECTED"
    NOT_CONFIGURED = "NOT_CONFIGURED"


class DeliveryStatus(StrEnum):
    DELIVERED = "DELIVERED"  # the guest can read it now (in-app: persisted and visible)
    QUEUED = "QUEUED"  # handed to a provider, delivery receipt pending
    FAILED = "FAILED"
    NOT_CONFIGURED = "NOT_CONFIGURED"


class ChannelNotConfiguredError(AppError):
    status_code = 422
    code = "channel_not_configured"
    message = "This messaging channel is not connected."


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    organization_id: uuid.UUID
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    content: str


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    status: DeliveryStatus
    provider_message_id: str | None = None
    detail: str | None = None

    def as_metadata(self, channel: Channel) -> dict[str, Any]:
        return {
            "channel": channel.value,
            "status": self.status.value,
            **(
                {"provider_message_id": self.provider_message_id}
                if self.provider_message_id
                else {}
            ),
        }


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """A provider message normalised into inbox terms."""

    channel: Channel
    provider_message_id: str
    sender_address: str
    content: str
    received_at: datetime
    raw_reference: dict[str, Any] = field(default_factory=dict)


class ChannelAdapter(ABC):
    channel: Channel
    label: str

    @property
    @abstractmethod
    def status(self) -> ChannelStatus: ...

    def assert_can_send(self) -> None:
        """Raise before anything is persisted if this channel can't deliver."""
        if self.status != ChannelStatus.CONNECTED:
            raise ChannelNotConfiguredError(
                f"{self.label} is not connected.", details={"channel": self.channel.value}
            )

    @abstractmethod
    def send_message(self, message: OutboundMessage) -> DeliveryResult: ...

    @abstractmethod
    def receive_message(self, message: InboundMessage) -> InboundMessage: ...

    @abstractmethod
    def normalize_event(self, payload: dict[str, Any]) -> InboundMessage | None: ...

    @abstractmethod
    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool: ...
