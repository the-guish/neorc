# Switch

A switch runs one of several named cases. This flow sends a gift for a VIP
booking.

```yaml
check_vip:
  handler: tasks:vip_case          # returns "is_vip" or "not_vip"
  params:
    booking: tasks.load_booking

route:
  switch:
    on: tasks.check_vip
    cases:
      is_vip:
        steps:
          compute_benefit:
            handler: tasks:vip_benefit
            params:
              booking: tasks.load_booking
          send_gift:
            handler: tasks:send_gift
            params:
              booking: tasks.load_booking
              benefit: tasks.compute_benefit
        output: tasks.send_gift
      not_vip:
        steps: {}
        output: null

confirm:
  handler: tasks:confirm
  params:
    notice: switches.route
```

`on` refers to a task in the same scope as the switch, or to a flow input.
Its value is a case name. Other values fail the run.

Each case needs two keys, `steps` and `output`. `steps` may be empty, and
`output` may be `null`. Any other `output` refers to a step inside the case.

A switch finishes once every step of the chosen case has succeeded. The switch
runs no other case.

Steps outside a case refer only to the switch's output, as `switches.<name>`.
That output is the chosen case's output. A flow can return a switch's output.

A switch adds no list level. It chooses a case again for each iteration or
branch of an enclosing loop or fan-out.

Each step has a unique name in the flow, including the steps of every case.
