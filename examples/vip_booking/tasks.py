# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The handlers of the vip_booking flow, a switch. See README.md.

Plain functions: the flow file in flows/ names each by import path, so nothing
here imports neorc.
"""

BOOKINGS = {
    1: {"guest": "Ada", "nights": 2},
    2: {"guest": "Grace", "nights": 12},
}

VIP_NIGHTS = 10


def load_booking(booking_id):
    return {"id": booking_id, **BOOKINGS[booking_id]}


def vip_case(booking):
    """The name of the case the switch runs."""
    return "is_vip" if booking["nights"] >= VIP_NIGHTS else "not_vip"


def vip_benefit(booking):
    return "champagne" if booking["nights"] < 2 * VIP_NIGHTS else "a spa day"


def send_gift(booking, benefit):
    return f"{benefit} is on its way to {booking['guest']}"


def confirm(booking, notice):
    """``notice`` is the switch's output: ``None`` when the gift case did not run."""
    confirmation = f"Booking {booking['id']} is confirmed"
    return confirmation + "." if notice is None else f"{confirmation}: {notice}."
