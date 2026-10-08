from __future__ import annotations

from app.channels.adapters import (
    AirbnbAdapter,
    BookingAdapter,
    HostawayAdapter,
    InAppAdapter,
    OtherChannelAdapter,
    TwilioAdapter,
    WhatsAppAdapter,
)
from app.channels.base import ChannelAdapter
from app.inbox.models import Channel

_ADAPTERS: dict[Channel, ChannelAdapter] = {
    adapter.channel: adapter
    for adapter in (
        InAppAdapter(),
        HostawayAdapter(),
        TwilioAdapter(),
        WhatsAppAdapter(),
        AirbnbAdapter(),
        BookingAdapter(),
        OtherChannelAdapter(),
    )
}


def get_adapter(channel: Channel) -> ChannelAdapter:
    return _ADAPTERS[channel]


def all_adapters() -> list[ChannelAdapter]:
    return list(_ADAPTERS.values())
