import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { detailOf } from "@oneberth/ui";
import { useEffect, useState, type KeyboardEvent } from "react";

import type { OptionPage } from "../context";

/** How long typing rests before the host is asked. */
const RESTS_MS = 200;

/** How long an answer serves the same search again. */
const FRESH_MS = 30_000;

/** What a search asks the host, the flow and the limit aside. */
export interface Asked {
  source: string;
  after: string | null;
  query: string;
  signal: AbortSignal;
}

export type Search = (asked: Asked) => Promise<OptionPage>;

/** `value`, once it has rested `ms` without changing. */
function useRested(value: string, ms: number): string {
  const [rested, setRested] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setRested(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return rested;
}

/**
 * A choice among options the host gives as the person types: a text box that
 * searches, and the options that match under it. `after` is the value of the
 * choice it comes after, if it names one: empty, the search waits for it.
 * The page draws one anew when `after` changes, so nothing typed outlives it.
 */
export function SearchChoice({
  id,
  label,
  source,
  after,
  afterLabel,
  value,
  onChoose,
  search,
}: {
  id: string;
  label: string;
  source: string;
  /** undefined when the choice comes after none. */
  after: string | undefined;
  afterLabel: string | undefined;
  value: string;
  onChoose: (value: string) => void;
  search: Search;
}) {
  // The label of the option chosen here; the value alone, for one the form started with.
  const [chosen, setChosen] = useState(value);
  // What is typed while the list is open; null while it is closed.
  const [typed, setTyped] = useState<string | null>(null);
  const [active, setActive] = useState(0);
  const query = useRested(typed ?? "", RESTS_MS);
  const waiting = after === "";
  const open = typed !== null && !waiting;
  const found = useQuery({
    queryKey: ["host-options", source, after ?? null, query],
    queryFn: ({ signal }) => search({ source, after: after ?? null, query, signal }),
    enabled: open,
    staleTime: FRESH_MS,
    retry: false,
    placeholderData: keepPreviousData,
  });
  const items = found.data?.items ?? [];
  const listId = `${id}-options`;

  function choose(option: { label: string; value: string }) {
    setChosen(option.label);
    setTyped(null);
    onChoose(option.value);
  }
  function key(event: KeyboardEvent<HTMLInputElement>) {
    if (!open) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((at) => Math.min(Math.max(at + step, 0), Math.max(items.length - 1, 0)));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const option = items[active];
      if (option !== undefined) choose(option);
    } else if (event.key === "Escape") {
      setTyped(null);
    }
  }

  const shown = value === "" ? "" : chosen;
  return (
    <div className="neorc-flow-form-search">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type="text"
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        autoComplete="off"
        disabled={waiting}
        placeholder={
          waiting ? `Choose ${afterLabel ?? "the choice above"} first` : shown || "Search…"
        }
        value={typed ?? shown}
        onFocus={() => {
          setTyped("");
          setActive(0);
        }}
        onBlur={() => setTyped(null)}
        onChange={(event) => {
          setTyped(event.target.value);
          setActive(0);
        }}
        onKeyDown={key}
      />
      {open && (
        <div className="neorc-flow-form-search-found">
          {found.isError ? (
            <p role="alert">The options could not be read: {detailOf(found.error)}.</p>
          ) : found.data === undefined ? (
            <p className="neorc-muted">Searching…</p>
          ) : items.length === 0 ? (
            <p className="neorc-muted">Nothing matches.</p>
          ) : (
            <ul id={listId} role="listbox" aria-label={label}>
              {items.map((option, index) => (
                <li
                  key={option.value}
                  role="option"
                  aria-selected={index === active}
                  // Before the box's blur, which would close the list first.
                  onMouseDown={(event) => {
                    event.preventDefault();
                    choose(option);
                  }}
                >
                  {option.label}
                </li>
              ))}
            </ul>
          )}
          {found.data?.more === true && (
            <p className="neorc-muted">More match: type more to narrow them.</p>
          )}
        </div>
      )}
    </div>
  );
}
