"""Concrete channel adapters.

* ``InAppAdapter`` — the guest portal. Delivery *is* persistence: once the message
  row commits, the guest's portal shows it. Connected.
* Provider adapters (Twilio SMS, WhatsApp, Hostaway, Airbnb, Booking.com) — the
  interface is in place, the integrations are not. They report
  ``NOT_CONFIGURED``, refuse to send and reject webhooks. Nothing is faked.
"""

from __future__ import annotations

from typing import Any

from app.channels.base import (
    ChannelAdapter,
    ChannelNotConfiguredError,
    ChannelStatus,
    DeliveryResult,
    DeliveryStatus,
    InboundMessage,
    OutboundMessage,
)
from app.inbox.models import Channel


class InAppAdapter(ChannelAdapter):
    channel = Channel.IN_APP
    label = "Guest portal"

    @property
    def status(self) -> ChannelStatus:
        return ChannelStatus.CONNECTED

    def send_message(self, message: OutboundMessage) -> DeliveryResult:
        # The message row is the delivery: the portal reads it through the guest API.
        return DeliveryResult(status=DeliveryStatus.DELIVERED)

    def receive_message(self, message: InboundMessage) -> InboundMessage:
        return message

    def normalize_event(self, payload: dict[str, Any]) -> InboundMessage | None:
        return None  # in-app messages arrive through the guest API, not webhooks

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        return False  # there is no in-app webhook


class NotConfiguredAdapter(ChannelAdapter):
    """Base for provider channels whose integration isn't connected yet."""

    provider: str

    @property
    def status(self) -> ChannelStatus:
        return ChannelStatus.NOT_CONFIGURED

    def _refuse(self) -> ChannelNotConfiguredError:
        return ChannelNotConfiguredError(
            f"{self.label} is not connected. Reply through the guest portal, "
            "or connect the integration first.",
            details={"channel": self.channel.value, "provider": self.provider},
        )

    def assert_can_send(self) -> None:
        raise self._refuse()

    def send_message(self, message: OutboundMessage) -> DeliveryResult:
        raise self._refuse()

    def receive_message(self, message: InboundMessage) -> InboundMessage:
        raise self._refuse()

    def normalize_event(self, payload: dict[str, Any]) -> InboundMessage | None:
        raise self._refuse()

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        return False


class TwilioAdapter(NotConfiguredAdapter):
    channel = Channel.SMS
    label = "SMS (Twilio)"
    provider = "twilio"


class WhatsAppAdapter(NotConfiguredAdapter):
    channel = Channel.WHATSAPP
    label = "WhatsApp"
    provider = "whatsapp"


class HostawayAdapter(NotConfiguredAdapter):
    """PMS-routed messaging (covers EMAIL / OTHER via Hostaway's unified inbox)."""

    channel = Channel.EMAIL
    label = "Email (Hostaway)"
    provider = "hostaway"


class AirbnbAdapter(NotConfiguredAdapter):
    channel = Channel.AIRBNB
    label = "Airbnb"
    provider = "airbnb"


class BookingAdapter(NotConfiguredAdapter):
    channel = Channel.BOOKING
    label = "Booking.com"
    provider = "booking"


class OtherChannelAdapter(NotConfiguredAdapter):
    channel = Channel.OTHER
    label = "Other channel"
    provider = "none"
