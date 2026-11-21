// What neorc's root component gives every page: the manager's API at the
// address the host named, whether the shell shows neorc, and the host's
// source of forms, of their options and of a run's start.
import { createContext, useContext } from "react";

import { createApi, type Api } from "./api";

/** The A2UI v0.9 messages of a form, as a flow's own form file holds them. */
export type FormMessages = readonly unknown[];

/** The flow a host is asked a form for: its latest version. */
export interface FormRequest {
  name: string;
  version: string;
  /** The flow's definition, as uploaded. */
  content: unknown;
}

/**
 * Where a host's forms to start a run come from. It answers the form for a
 * flow, or null for a flow it has no form for: neorc then draws its own.
 */
export type FormSource = (
  flow: FormRequest,
) => FormMessages | null | Promise<FormMessages | null>;

/**
 * What a host is asked for the options of a choice in its form that names a
 * source of its own (`optionsFrom`), rather than listing them.
 */
export interface OptionRequest {
  /** The flow whose form holds the choice. */
  flow: string;
  /** The choice's `optionsFrom.source`: a name the host gives a meaning to. */
  source: string;
  /** The value of the choice `optionsFrom.after` names; null when it names none. */
  after: string | null;
  /** What the person typed: the options that match it. */
  query: string;
  /** The most options to answer. */
  limit: number;
  /** Aborted when the person typed on, and the answer is no longer wanted. */
  signal: AbortSignal;
}

/** The options that match a request, at most its limit; `more` when others match too. */
export interface OptionPage {
  items: readonly { label: string; value: string }[];
  more: boolean;
}

/** Where the options of a host's form come from, for a choice that names a source. */
export type OptionSource = (asked: OptionRequest) => Promise<OptionPage>;

/**
 * How a host starts a run of a flow it gave the form for, in the place of
 * the manager's own route: the host checks the inputs its form asked for.
 * It answers the run's id, or throws what to show.
 */
export type RunStarter = (
  flow: string,
  inputs: Record<string, unknown>,
) => Promise<{ id: string }>;

export interface Neorc {
  api: Api;
  /** Whether the shell shows neorc: a hidden neorc polls nothing. */
  visible: boolean;
  forms: FormSource | undefined;
  options: OptionSource | undefined;
  start: RunStarter | undefined;
}

/** Outside the root component, as in a test of one page: standalone neorc, shown. */
export const NeorcContext = createContext<Neorc>({
  api: createApi("/neorc/"),
  visible: true,
  forms: undefined,
  options: undefined,
  start: undefined,
});

export function useApi(): Api {
  return useContext(NeorcContext).api;
}

export function useVisible(): boolean {
  return useContext(NeorcContext).visible;
}

export function useFormSource(): FormSource | undefined {
  return useContext(NeorcContext).forms;
}

export function useOptionSource(): OptionSource | undefined {
  return useContext(NeorcContext).options;
}

export function useRunStarter(): RunStarter | undefined {
  return useContext(NeorcContext).start;
}
