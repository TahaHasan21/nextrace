import { Location } from '@angular/common';

/**
 * Href for an element on the current page (e.g. "evidence-ev1_...").
 *
 * A bare "#id" is not enough: index.html sets <base href="/">, so "#id"
 * resolves to "/#id", which drops the current "?event=<id>" query. The
 * browser then loads a new document - losing the open investigation and
 * the operator key held in memory. Keeping the current (base-relative)
 * path and query means only the fragment changes, so following the link
 * just scrolls within the page.
 */
export function inPageHref(location: Location, elementId: string): string {
  return `${location.path()}#${elementId}`;
}
