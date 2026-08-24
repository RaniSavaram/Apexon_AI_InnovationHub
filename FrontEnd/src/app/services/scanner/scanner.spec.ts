import { TestBed } from '@angular/core/testing';

import { Scanner } from './scanner';

describe('Scanner', () => {
  let service: Scanner;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    service = TestBed.inject(Scanner);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });
});
