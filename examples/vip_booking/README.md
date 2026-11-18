# vip_booking

A switch. The flow sends a gift for a VIP booking, and no gift for any other
booking. It is the flow of [docs/specs/switch.md](../../docs/specs/switch.md).

`check_vip` returns the name of a case, `is_vip` or `not_vip`. The switch
`route` runs the steps of that case and no other. `confirm` is outside the
switch and reads `switches.route`, the output of the case that ran. The
output of `not_vip` is `null`.

Run it in one process, with nothing to deploy, from the repository root after
`uv sync`. Booking 2 is a VIP booking:

    uv run neorc run examples/vip_booking --flow vip_booking \
        --inputs '{"booking_id": 2}'

It prints `"Booking 2 is confirmed: champagne is on its way to Grace."`.
Booking 1 is not a VIP booking:

    uv run neorc run examples/vip_booking --flow vip_booking \
        --inputs '{"booking_id": 1}'

It prints `"Booking 1 is confirmed."`, and `compute_benefit` and `send_gift`
never run.

[python/neorc-core/tests/test_examples.py](../../python/neorc-core/tests/test_examples.py)
runs both and asserts what they produce, in memory;
[python/neorc/tests/test_end_to_end.py](../../python/neorc/tests/test_end_to_end.py)
runs them deployed. To deploy it by hand, follow
[hello](../hello/README.md#deployed) with `examples/vip_booking` in the place
of `examples/hello`.
