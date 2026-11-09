/**
 * Whether the page is framed by a host application that draws the navigation
 * itself: `?embedded` in the page's own URL, before the hash, as in
 * `/neorc/ui/?embedded#/runs`.
 *
 * Embedded, the shell leaves out its sidebar, and with it the brand, the menu
 * button, the flows, the latest runs and the profile: the host's own panel
 * links to the same hashes. The page and the banner stay. Nothing else
 * changes: the routes, the API one level above the page and the
 * Content-Security-Policy are the same either way.
 */
export function isEmbedded(search: string = location.search): boolean {
  return new URLSearchParams(search).has("embedded");
}
