import { Component, Input } from '@angular/core';

import { Evidence, NextraceEvent } from '../../../models/investigation.models';
import { formatTimeOnly, humanizeEventType } from '../../../shared/format-metadata';

const EVIDENCE_TYPE_LABELS: Record<string, string> = {
  temporal_proximity: 'Temporal relationship',
  sequence_relationship: 'Sequence relationship',
  recovery_relationship: 'Recovery relationship',
};

export interface RelatedEventSummary {
  id: number;
  time: string;
  label: string;
}

@Component({
  selector: 'app-evidence',
  templateUrl: './evidence.component.html',
  styleUrl: './evidence.component.css',
})
export class EvidenceComponent {
  @Input({ required: true }) items: Evidence[] = [];
  /** Resolves each evidence item's event_ids against the already-loaded
   * timeline - the evidence panel never issues its own API request. */
  @Input({ required: true }) timeline: NextraceEvent[] = [];

  protected labelFor(type: string): string {
    return EVIDENCE_TYPE_LABELS[type] ?? type;
  }

  protected relatedEvents(item: Evidence): RelatedEventSummary[] {
    const byId = new Map(this.timeline.map((event) => [event.id, event]));
    return item.event_ids
      .map((id) => byId.get(id))
      .filter((event): event is NextraceEvent => event !== undefined)
      .sort((a, b) => a.timestamp.localeCompare(b.timestamp))
      .map((event) => ({
        id: event.id,
        time: formatTimeOnly(event.timestamp),
        label: humanizeEventType(event.event_type),
      }));
  }
}
