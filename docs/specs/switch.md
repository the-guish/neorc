# Switch

A switch runs one of several named cases. This flow sends a gift for a VIP
booking.

```yaml
load_booking:
  handler: tasks:load_booking
  params:
    booking_id: inputs.booking_id

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

## Choosing a case

`on` gives the name of a case, as a string. Other values fail the run.

`on` refers to a flow input, a task, a sub-flow or another switch. A referenced
step is outside the switch, and outside every loop and fan-out the switch is
outside of. So a task before a fan-out can choose a case in each branch.

`on` reads one value: the referenced step's result in the switch's own
iteration and branch. A loop's exit condition reads its task the same way.

`on` takes only `string` flow inputs. `on` cannot refer to a task with a form,
whose result is a mapping.

Case names follow the rules of step names. They start with a letter or `_`,
then letters, digits or `_`. YAML reads `true` and `false` as booleans. Quotes
keep them strings in a case name.

## Cases and output

Each case needs two keys, `steps` and `output`. `steps` may be empty, and
`output` may be `null`. Otherwise `output` refers to a step inside the case.

`output` reads that step's result in the switch's own iteration and branch,
like `on`. A loop or fan-out inside the case adds its list level as usual.

`output` is required in a case, unlike in a flow. `switches.<name>` resolves to
it. Each case thus states its result, even nothing.

A switch finishes once every step of the chosen case has succeeded. The switch
runs no other case.

## References

A case's steps refer to each other and to steps outside the switch. Steps
outside a case refer only to the switch's output, as `switches.<name>`. This
includes the steps of other cases.

`switches.<name>` resolves to the chosen case's output. It is for steps outside
the switch. A step inside would wait on itself. Flows can return a switch's
output.

A switch adds no list level. It chooses a case again for each iteration or
branch of an enclosing loop or fan-out.

## Inside a loop

A switch has an output in every iteration. Readers in the loop get one entry
per iteration, as for a task.

A case may run in some iterations only. Its steps still read each other as
lists of iterations so far. An iteration where the case did not run gives
`null`. Position *i* stays iteration *i*.

For example, a loop chooses `not_vip` in iteration 1 and `is_vip` in
iteration 2. In iteration 2, `send_gift` reads `tasks.compute_benefit` as
`[null, <benefit>]`.

## Names

Each step has a unique name in the flow, including the steps of every case.
