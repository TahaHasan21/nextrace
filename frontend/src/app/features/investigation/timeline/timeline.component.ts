import { DatePipe } from '@angular/common';
import { Component, Input } from '@angular/core';

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
