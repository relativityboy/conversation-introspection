/** `project_slug` is the CLI's raw source-directory name, e.g. "-Users-x-proj". The display name
 * is the tail after the last "-Users-" — deliberately not a full path reconstruction (the CLI's
 * dash-collapsing isn't reliably reversible); the simple, robust cut. Shared by the sidebar
 * row eyebrow and the project-tree row label (spec §4.4). */
export function projectDisplayName(slug: string): string {
  const marker = '-Users-'
  const idx = slug.lastIndexOf(marker)
  return idx === -1 ? slug : slug.slice(idx + marker.length)
}
