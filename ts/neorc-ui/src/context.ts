// What neorc's root component gives every page: the manager's API at the
// address the host named, whether the shell shows neorc, and the host's
// source of forms.
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

export interface Neorc {
  api: Api;
  /** Whether the shell shows neorc: a hidden neorc polls nothing. */
  visible: boolean;
  forms: FormSource | undefined;
}

/** Outside the root component, as in a test of one page: standalone neorc, shown. */
export const NeorcContext = createContext<Neorc>({
  api: createApi("/neorc/"),
  visible: true,
  forms: undefined,
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
