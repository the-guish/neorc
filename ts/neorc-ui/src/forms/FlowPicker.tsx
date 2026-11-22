// A choice of one of the manager's flows, for a form: a text box that asks
// the manager for the flows whose name holds what is typed, and a list of
// them under it to pick from. The catalog's own component, not A2UI's:
// docs/architecture/forms.md.
import { detailOf } from "@oneberth/ui";
import { useEffect, useState, type KeyboardEvent } from "react";

import type { Flow } from "../api";
import { useFlowSearch } from "../queries";

/** How long the box waits after a letter before it asks the manager. */
export const SEARCH_DELAY_MS = 200;

/** `value`, once it has stayed the same for `ms`. */
function useSettled<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return settled;
}

/**
 * Of `flows`, those whose name holds `text`, in the manager's order: in any
 * case, those that start with it first.
 */
function holding(flows: readonly Flow[], text: string): Flow[] {
  const held = text.toLowerCase();
  const found = flows.filter((flow) => flow.name.toLowerCase().includes(held));
  const starts = (flow: Flow) => flow.name.toLowerCase().startsWith(held);
  return [...found.filter(starts), ...found.filter((flow) => !starts(flow))];
}

/**
 * The box holds what the person types, and `value` the flow chosen: a flow
 * picked from the list, or one whose name was typed in full. Typing anything
 * else leaves no flow chosen, so a form submits the name of a flow or
 * nothing.
 *
 * The list lies over what follows the box, so it shows flows to pick and
 * nothing else: while the box has the focus and does not hold the flow
 * chosen. What there is to say without a flow to pick is a line under the
 * box, which covers nothing.
 *
 * The list always fits the text. While the manager's answer for a text is
 * on its way, the list is the answer before it, narrowed here to the flows
 * that hold the text: what Enter picks is a flow the person sees.
 */
export function FlowPicker({
  id,
  label,
  value,
  onChange,
}: {
  id: string;
  label: string;
  /** The name of the flow chosen; empty while none is. */
  value: string;
  onChange: (name: string) => void;
}) {
  const [text, setText] = useState(value);
  const [focused, setFocused] = useState(false);
  /** The option the arrow keys are on; -1 for none. */
  const [active, setActive] = useState(-1);
  /** Escape puts the list away until the box is typed in or left. */
  const [away, setAway] = useState(false);
  const typed = text.trim();
  const chosen = value !== "" && value === typed;
  const unchosen = typed !== "" && value === "";
  const looking = focused && !chosen;
  // Asked while the box looks for a flow, and while a name typed is not a
  // flow chosen yet: a person may type a name in full and leave the box.
  const asked = useSettled(typed, SEARCH_DELAY_MS);
  const search = useFlowSearch(asked, looking || unchosen);
  // Whether the manager's answer is for what the box holds now: while the
  // typing rests and the manager answers, it is still the one before.
  const current = asked === typed && search.isSuccess && !search.isPlaceholderData;
  const answer = search.data ?? [];
  const flows = current ? answer : holding(answer, typed);
  const open = looking && !away && flows.length > 0;

  // A name typed in full is the flow chosen, as if picked from the list.
  const named = unchosen && flows.some((flow) => flow.name === typed);
  useEffect(() => {
    if (named) onChange(typed);
  }, [named, typed, onChange]);

  function pick(name: string) {
    setText(name);
    onChange(name);
  }

  function type(next: string) {
    setText(next);
    setAway(false);
    // The first flow found is the one Enter picks.
    setActive(0);
    if (value !== "" && next.trim() !== value) onChange("");
  }

  const at = active < flows.length ? active : -1;
  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (open) setActive(Math.min(at + 1, flows.length - 1));
        else setAway(false);
        break;
      case "ArrowUp":
        event.preventDefault();
        if (open) setActive(Math.max(at - 1, 0));
        break;
      case "Enter": {
        const flow = open ? flows[at] : undefined;
        if (flow !== undefined) {
          event.preventDefault();
          pick(flow.name);
        } else if (looking && !current && !search.isError) {
          // The answer for this text is on its way: the form is not
          // submitted before it comes.
          event.preventDefault();
        }
        // Otherwise Enter is the form's: it submits.
        break;
      }
      case "Escape":
        if (open) {
          event.preventDefault();
          setAway(true);
        }
        break;
    }
  }

  const list = `${id}-flows`;
  /** What there is to say when there is no flow to pick, or none picked. */
  let said: string | null = null;
  if (looking || unchosen) {
    if (search.isError) {
      said = `The flows could not be searched: ${detailOf(search.error)}`;
    } else if (flows.length === 0) {
      said = current ? "No flow matches." : "Searching…";
    } else if (!focused) {
      said = "No flow is chosen: pick one from the list.";
    }
  }
  return (
    <div className="neorc-flow-form-picker">
      <label htmlFor={id}>{label}</label>
      {/* The box and its list, apart from the label: the list hangs from the
          box, and its flows are no part of the box's name. */}
      <div className="neorc-flow-form-picker-box">
        <input
          id={id}
          type="text"
          role="combobox"
          aria-expanded={open}
          aria-controls={open ? list : undefined}
          aria-autocomplete="list"
          aria-activedescendant={open && at >= 0 ? `${list}-${at}` : undefined}
          autoComplete="off"
          spellCheck={false}
          placeholder="Type to search the flows"
          value={text}
          onChange={(event) => type(event.target.value)}
          onFocus={() => {
            setFocused(true);
            setActive(-1);
          }}
          onBlur={() => {
            setFocused(false);
            setAway(false);
          }}
          onKeyDown={onKeyDown}
        />
        {open && (
          <ul id={list} role="listbox" aria-label={`Flows for ${label}`} aria-busy={!current}>
            {flows.map((flow, index) => (
              <li
                key={flow.name}
                id={`${list}-${index}`}
                role="option"
                aria-selected={index === at}
                className={index === at ? "neorc-flow-form-picker-active" : undefined}
                // The box keeps the focus, so the list stays for the click.
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => pick(flow.name)}
              >
                {flow.name} <small className="neorc-muted">{flow.version}</small>
              </li>
            ))}
          </ul>
        )}
      </div>
      {said !== null && (
        <small role="status" className="neorc-muted">
          {said}
        </small>
      )}
    </div>
  );
}
