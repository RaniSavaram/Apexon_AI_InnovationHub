import { Component, OnInit, inject } from '@angular/core';
import { RouterOutlet, Router } from '@angular/router';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet],
  templateUrl: './app.html',
  styleUrl: './app.css'
})
export class AppComponent implements OnInit {
  private router = inject(Router);

  ngOnInit(): void {
    // Clear any session storage on full page refresh/load
    try {
      if (typeof window !== 'undefined' && window.sessionStorage) {
        window.sessionStorage.clear();
      }
    } catch (e) {}

    // Whenever the page is refreshed, immediately route to the Home page
    this.router.navigate(['/'], { replaceUrl: true });
  }
}