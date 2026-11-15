// `@neorc/ui`: neorc's interface as a node package (oneberth's shell.md,
// "The neorc interface as a package"). This entry is light: it registers
// neorc with the shell, and the root component loads the first time neorc's
// icon is chosen. `@neorc/ui/app` is the root component itself.
import type { AppModule, AppProps, CountStore, ShellApp } from "@oneberth/ui";

import type { NeorcOptions } from "./NeorcApp";

export type { NeorcAppProps, NeorcOptions } from "./NeorcApp";
export type { FormMessages, FormRequest, FormSource } from "./context";
// The reader of a form, for a host's tests: a host checks the form it gives
// neorc against the subset neorc draws. A host's bundle leaves it out unless
// the host calls it; neorc's own chunk holds it.
export { formOf, type Form } from "./forms/subset";

/** How a host registers neorc with the shell. */
export interface NeorcShellOptions extends NeorcOptions {
  /** The name of its routes, `#/<name>/…`: `neorc` unless the host says. */
  name?: string;
  /** What the rail calls it: `neorc` unless the host says. */
  title?: string;
  /** The count on its icon. */
  count?: CountStore;
}

/**
 * neorc as an application of oneberth's shell: its name, title and icon, and
 * the loader of its root component, given `api` and `forms`.
 */
export function neorcApp({ api, forms, name = "neorc", title = "neorc", count }: NeorcShellOptions): ShellApp {
  const app: ShellApp = {
    name,
    title,
    icon: <NeorcIcon />,
    load: async (): Promise<AppModule> => {
      const { NeorcApp } = await import("./NeorcApp");
      return {
        default: function Neorc(props: AppProps) {
          return <NeorcApp {...props} api={api} forms={forms} />;
        },
      };
    },
  };
  if (count !== undefined) app.count = count;
  return app;
}

/** neorc's icon on the rail: a flow of three steps, in the current colour. */
export function NeorcIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="20"
      height="20"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="6" cy="5" r="2.5" />
      <circle cx="6" cy="19" r="2.5" />
      <circle cx="18" cy="9" r="2.5" />
      <path d="M6 7.5v9M18 11.5c0 3.5-4.5 3-12 5" />
    </svg>
  );
}
