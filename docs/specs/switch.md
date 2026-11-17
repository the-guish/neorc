# Switch

A switch runs one of several named cases. Its `on` reference gives the name of
the case to run:

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
Its value is a case name. Any other value fails the run.

Each case has `steps` and an `output`. A case may have no steps. The `output`
key is required, and its value may be `null`. A non-null output refers to a
step inside the case.

A switch is finished once every step of the chosen case has succeeded. The
other cases never run.

Steps outside a case refer only to the switch's output, as `switches.<name>`.
That output is the chosen case's output, or `null`. A flow's own `output` can
refer to a switch the same way.

A switch adds no list level. Inside a loop or a fan-out, it chooses a case for
each iteration or branch.

Step names are unique across the whole flow, cases included.
