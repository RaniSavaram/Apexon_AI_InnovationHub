import { Injectable } from '@angular/core';

@Injectable({ providedIn: 'root' })
export class Api {
  // Django serves the built Angular app from the same origin, so relative
  // paths work directly - no absolute host/port needed here.
  readonly baseUrl = 'http://127.0.0.1:8000/api';
}
