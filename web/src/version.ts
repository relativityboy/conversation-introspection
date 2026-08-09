// NOTE(claude): the version is baked at BUILD time, deliberately: a stale dist then
// reports its own stale version instead of borrowing currency from the API (spec §3).
// The typeof guard keeps non-vite tools (and tests that mock this module) safe.
export const UI_VERSION: string =
  typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'unknown'
