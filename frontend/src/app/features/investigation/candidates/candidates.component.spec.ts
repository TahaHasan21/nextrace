import { TestBed } from '@angular/core/testing';

import { Candidate, Evidence } from '../../../models/investigation.models';
import { CandidatesComponent } from './candidates.component';

function makeCandidate(overrides: Partial<Candidate>): Candidate {
  return {
    event_id: 1,
    event_type: 'deployment',
    score: 90,
    reasons: ['Occurred 4 minutes before the incident.'],
    supporting_evidence_ids: [],
    ...overrides,
  };
}

function makeEvidence(overrides: Partial<Evidence>): Evidence {
  return {
    id: 'ev1_a1a1a1a1a1a1a1a1',
    type: 'sequence_relationship',
    description: 'A deployment was followed by an error_spike.',
    event_ids: [1, 4],
    ...overrides,
  };
}

describe('CandidatesComponent', () => {
  it('renders candidates ranked with score and reasons, in the order given', () => {
    const candidates: Candidate[] = [
      makeCandidate({ event_id: 1, event_type: 'deployment', score: 90 }),
      makeCandidate({ event_id: 3, event_type: 'db_latency', score: 80 }),
    ];

    const fixture = TestBed.createComponent(CandidatesComponent);
    fixture.componentRef.setInput('candidates', candidates);
    fixture.componentRef.setInput('evidence', []);
    fixture.detectChanges();

    const items = fixture.nativeElement.querySelectorAll('.candidate-item');
    expect(items.length).toBe(2);
    expect(items[0].textContent).toContain('#1');
    expect(items[0].textContent).toContain('Deployment');
    expect(items[0].textContent).toContain('Score: 90');
    expect(items[0].textContent).toContain('Occurred 4 minutes before the incident.');
    expect(items[1].textContent).toContain('#2');
    expect(items[1].textContent).toContain('Db Latency');
  });

  it('does not display a probability or confidence percentage', () => {
    const fixture = TestBed.createComponent(CandidatesComponent);
    fixture.componentRef.setInput('candidates', [makeCandidate({})]);
    fixture.componentRef.setInput('evidence', []);
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent as string;
    expect(text).not.toMatch(/\d+%\s*confidence/i);
  });

  it('communicates that candidate scores are heuristics, not confirmed root causes', () => {
    const fixture = TestBed.createComponent(CandidatesComponent);
    fixture.componentRef.setInput('candidates', [makeCandidate({})]);
    fixture.componentRef.setInput('evidence', []);
    fixture.detectChanges();

    const text = (fixture.nativeElement.textContent as string).toLowerCase();
    expect(text).toContain('evidence-based investigation heuristics');
    expect(text).toContain('not probabilities or confirmed root causes');
  });

  it('resolves supporting_evidence_ids against the evidence list, not raw event ids', () => {
    const evidenceItem = makeEvidence({
      id: 'ev1_a82f000000000001',
      type: 'sequence_relationship',
      description: 'A deployment was followed by an error_spike.',
    });
    const candidate = makeCandidate({ event_id: 1, supporting_evidence_ids: [evidenceItem.id] });

    const fixture = TestBed.createComponent(CandidatesComponent);
    fixture.componentRef.setInput('candidates', [candidate]);
    fixture.componentRef.setInput('evidence', [evidenceItem]);
    fixture.detectChanges();

    const referencesSection = fixture.nativeElement.querySelector('.supporting-references');
    expect(referencesSection).not.toBeNull();
    expect(referencesSection.textContent).toContain('Supporting evidence');
    expect(referencesSection.textContent).toContain('Sequence relationship');

    const link = referencesSection.querySelector('.evidence-link');
    expect(link).not.toBeNull();
    expect(link.getAttribute('href')).toBe(`#evidence-${evidenceItem.id}`);
  });

  it('ignores a supporting_evidence_ids entry that does not resolve to a real evidence item', () => {
    const candidate = makeCandidate({ event_id: 1, supporting_evidence_ids: ['ev1_doesnotexist'] });

    const fixture = TestBed.createComponent(CandidatesComponent);
    fixture.componentRef.setInput('candidates', [candidate]);
    fixture.componentRef.setInput('evidence', []);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.supporting-references')).toBeNull();
  });

  it('omits the supporting-references block when there are no resolvable references', () => {
    const fixture = TestBed.createComponent(CandidatesComponent);
    fixture.componentRef.setInput('candidates', [makeCandidate({ supporting_evidence_ids: [] })]);
    fixture.componentRef.setInput('evidence', []);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.supporting-references')).toBeNull();
  });

  it('shows an empty state when there are no candidates', () => {
    const fixture = TestBed.createComponent(CandidatesComponent);
    fixture.componentRef.setInput('candidates', []);
    fixture.componentRef.setInput('evidence', []);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.empty-state')).not.toBeNull();
  });
});
