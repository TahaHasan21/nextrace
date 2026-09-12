import { TestBed } from '@angular/core/testing';

import { Evidence, NextraceEvent } from '../../../models/investigation.models';
import { EvidenceComponent } from './evidence.component';

function makeEvent(overrides: Partial<NextraceEvent>): NextraceEvent {
  return {
    id: 1,
    service: 'payment-service',
    environment: 'production',
    event_type: 'deployment',
    timestamp: '2026-09-06T10:00:00Z',
    severity: 'info',
    source: 'github',
    message: 'event',
    metadata: null,
    created_at: '2026-09-06T10:00:01Z',
    ...overrides,
  };
}

describe('EvidenceComponent', () => {
  it('renders each evidence item with a human-readable type label', () => {
    const items: Evidence[] = [
      {
        id: 'ev1_a1a1a1a1a1a1a1a1',
        type: 'sequence_relationship',
        description: 'A deployment was followed by an error_spike.',
        event_ids: [1, 2],
      },
      {
        id: 'ev1_b2b2b2b2b2b2b2b2',
        type: 'temporal_proximity',
        description: 'Deployment occurred 180 seconds before error_spike.',
        event_ids: [1, 2],
      },
    ];

    const fixture = TestBed.createComponent(EvidenceComponent);
    fixture.componentRef.setInput('items', items);
    fixture.componentRef.setInput('timeline', []);
    fixture.detectChanges();

    const rendered = fixture.nativeElement.querySelectorAll('.evidence-item');
    expect(rendered.length).toBe(2);
    expect(rendered[0].textContent).toContain('Sequence relationship');
    expect(rendered[0].textContent).toContain('A deployment was followed by an error_spike.');
    expect(rendered[1].textContent).toContain('Temporal relationship');
  });

  it('shows an empty state when there is no evidence', () => {
    const fixture = TestBed.createComponent(EvidenceComponent);
    fixture.componentRef.setInput('items', []);
    fixture.componentRef.setInput('timeline', []);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.empty-state')).not.toBeNull();
  });

  it('never renders causal wording', () => {
    const items: Evidence[] = [
      {
        id: 'ev1_c3c3c3c3c3c3c3c3',
        type: 'recovery_relationship',
        description: 'A rollback was followed by recovery after an incident.',
        event_ids: [1, 2, 3],
      },
    ];

    const fixture = TestBed.createComponent(EvidenceComponent);
    fixture.componentRef.setInput('items', items);
    fixture.componentRef.setInput('timeline', []);
    fixture.detectChanges();

    const text = (fixture.nativeElement.textContent as string).toLowerCase();
    expect(text).not.toContain('caused');
    expect(text).not.toContain('resulted in');
  });

  it('resolves event_ids against the supplied timeline instead of issuing a new request', () => {
    const timeline: NextraceEvent[] = [
      makeEvent({ id: 1, event_type: 'config_change', timestamp: '2026-09-06T10:01:00Z' }),
      makeEvent({ id: 2, event_type: 'db_latency', timestamp: '2026-09-06T10:02:00Z' }),
    ];
    const items: Evidence[] = [
      {
        id: 'ev1_d4d4d4d4d4d4d4d4',
        type: 'sequence_relationship',
        description: 'A config_change was followed by a db_latency.',
        event_ids: [1, 2],
      },
    ];

    const fixture = TestBed.createComponent(EvidenceComponent);
    fixture.componentRef.setInput('items', items);
    fixture.componentRef.setInput('timeline', timeline);
    fixture.detectChanges();

    const related = fixture.nativeElement.querySelector('.related-events');
    expect(related.textContent).toContain('10:01');
    expect(related.textContent).toContain('Config Change');
    expect(related.textContent).toContain('10:02');
    expect(related.textContent).toContain('Db Latency');
  });

  it('displays each evidence item id and exposes it as a scroll anchor', () => {
    const items: Evidence[] = [
      {
        id: 'ev1_f6f6f6f6f6f6f6f6',
        type: 'sequence_relationship',
        description: 'A deployment was followed by an error_spike.',
        event_ids: [1, 2],
      },
    ];

    const fixture = TestBed.createComponent(EvidenceComponent);
    fixture.componentRef.setInput('items', items);
    fixture.componentRef.setInput('timeline', []);
    fixture.detectChanges();

    const item = fixture.nativeElement.querySelector('.evidence-item');
    expect(item.id).toBe('evidence-ev1_f6f6f6f6f6f6f6f6');
    expect(item.textContent).toContain('ev1_f6f6f6f6f6f6f6f6');
  });

  it('links each related event to its timeline anchor, so it can be located there', () => {
    const timeline: NextraceEvent[] = [
      makeEvent({ id: 21, event_type: 'deployment', timestamp: '2026-09-06T10:00:00Z' }),
      makeEvent({ id: 24, event_type: 'error_spike', timestamp: '2026-09-06T10:03:00Z' }),
    ];
    const items: Evidence[] = [
      {
        id: 'ev1_g7g7g7g7g7g7g7g7',
        type: 'sequence_relationship',
        description: 'A deployment was followed by an error_spike.',
        event_ids: [21, 24],
      },
    ];

    const fixture = TestBed.createComponent(EvidenceComponent);
    fixture.componentRef.setInput('items', items);
    fixture.componentRef.setInput('timeline', timeline);
    fixture.detectChanges();

    const links: NodeListOf<HTMLAnchorElement> = fixture.nativeElement.querySelectorAll(
      '.related-events-list .related-event-link',
    );
    expect(links.length).toBe(2);
    expect(links[0].getAttribute('href')).toBe('#event-21');
    expect(links[1].getAttribute('href')).toBe('#event-24');
  });

  it('silently omits event ids that are not present in the timeline', () => {
    const timeline: NextraceEvent[] = [makeEvent({ id: 1 })];
    const items: Evidence[] = [
      {
        id: 'ev1_e5e5e5e5e5e5e5e5',
        type: 'sequence_relationship',
        description: 'desc',
        event_ids: [1, 999],
      },
    ];

    const fixture = TestBed.createComponent(EvidenceComponent);
    fixture.componentRef.setInput('items', items);
    fixture.componentRef.setInput('timeline', timeline);
    fixture.detectChanges();

    const entries = fixture.nativeElement.querySelectorAll('.related-events-list li');
    expect(entries.length).toBe(1);
  });
});
