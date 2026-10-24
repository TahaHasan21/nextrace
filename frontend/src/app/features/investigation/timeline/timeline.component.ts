import { DatePipe } from '@angular/common';
import { Component, EventEmitter, Input, Output } from '@angular/core';

import { NextraceEvent } from '../../../models/investigation.models';
import { formatMetadataEntries, humanizeEventType, MetadataEntry } from '../../../shared/format-metadata';

@Component({
  selector: 'app-timeline',
  imports: [DatePipe],
  templateUrl: './timeline.component.html',
  styleUrl: './timeline.component.css',
})
export class TimelineComponent {
  @Input({ required: true }) events: NextraceEvent[] = [];
  @Input({ required: true }) targetEventId!: number;
  /** Emits the ID of another timeline event the user wants to investigate
   * in its own right (e.g. pivot from an error spike to the deployment). */
  @Output() readonly investigateEvent = new EventEmitter<number>();

  protected labelFor(eventType: string): string {
    return humanizeEventType(eventType);
  }

  protected metadataEntries(event: NextraceEvent): MetadataEntry[] {
    return formatMetadataEntries(event.metadata);
  }

  protected hasMetadata(event: NextraceEvent): boolean {
    return this.metadataEntries(event).length > 0;
  }
}
