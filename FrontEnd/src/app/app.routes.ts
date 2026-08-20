import { Routes } from '@angular/router';

import { MainLayoutComponent } from './layouts/main-layout/main-layout';
import { HomeComponent } from './pages/home/home';
import { DbScannerComponent } from './pages/db-scanner/db-scanner';
import { BiMigratorComponent } from './pages/bi-migrator/bi-migrator';
import { MedicationComponent } from './pages/medication/medication';

export const routes: Routes = [
  {
    path: '',
    component: MainLayoutComponent,
    children: [
      {
        path: '',
        component: HomeComponent
      },
      {
        path: 'dbscanner',
        component: DbScannerComponent
      },
      {
        path: 'bimigrator',
        component: BiMigratorComponent
      },
      {
        path: 'medication',
        component: MedicationComponent
      }
    ]
  },
  {
    path: '**',
    redirectTo: ''
  }
];