import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Api } from '../api/api';

export interface ConnectionDetails {
  server: string;
  database: string;
  username: string;
  password: string;
  /** Databricks-only: SQL warehouse endpoint, sent to Django as extra.http_path */
  httpPath?: string;
}

export interface ConnectResponse {
  status: string;
  message: string;
  source?: string;
}

export interface SavedConnectionProfile {
  server: string;
  database: string;
  username: string;
  password: string;
  extra?: { http_path?: string };
}

export interface SavedConnectionResponse {
  status: string;
  found: boolean;
  connection?: SavedConnectionProfile;
}

export interface SavedConnectionsResponse {
  status: string;
  connections: SavedConnectionProfile[];
}

export interface StartScanResponse {
  status: string;
  message?: string;
  scan_id?: string;
}

export interface ScanStatus {
  'token info': Array<{
    total?: number;
    prompt?: number;
    completion?: number;
    cost?: string;
  }>;
  'scan info': string[];
  progressbar: number;
  scan_status_message?: string;
  status?: 'Running' | 'Completed' | 'Failed';
  error?: string;
  source?: string;
  destination?: string;
  tables_found?: number;
  scan_id?: string;
  Logs?: Record<string, unknown>;
  result?: {
    output_files?: {
      assessment_report?: string;
      migration_plan?: string;
    };
  };
}

@Injectable({ providedIn: 'root' })
export class Scanner {
  private http = inject(HttpClient);
  private api = inject(Api);

  /**
   * Calls Django's connect_database view.
   * NOTE: that view reads FLAT fields off the request body
   * (request.data.get("server"), .get("database"), etc.) - not a nested
   * "connection" object - so the payload here is deliberately flat to match.
   */
  connectDatabase(source: string, connection: ConnectionDetails, rememberMe = false) {
    const payload = {
      source,
      server: connection.server,
      database: connection.database,
      username: connection.username,
      password: connection.password,
      extra: connection.httpPath ? { http_path: connection.httpPath } : {},
      remember_me: rememberMe,
    };
    return this.http.post<ConnectResponse>(`${this.api.baseUrl}/connect/`, payload);
  }

  /**
   * Looks up the last-saved connection details for a source (Django saves
   * them automatically after a successful connectDatabase() call), so the
   * connect form can be pre-filled instead of starting blank every time.
   */
  getSavedConnection(source: string) {
    return this.http.get<SavedConnectionResponse>(`${this.api.baseUrl}/connection/`, {
      params: { source },
    });
  }

  /**
   * Looks up every saved connection profile for a source (e.g. all known
   * Databricks servers), so the connect form can offer a dropdown picker
   * instead of pre-filling from just the last-used one.
   */
  getSavedConnections(source: string) {
    return this.http.get<SavedConnectionsResponse>(`${this.api.baseUrl}/connections/`, {
      params: { source },
    });
  }

  /**
   * Calls Django's start_scan view, which kicks the scan off in a
   * background thread and returns immediately with a scan_id. Use
   * getScanStatus() below to poll for progress until it completes.
   */
  startScan(source: string, destination: string, connection: ConnectionDetails) {
    const payload = {
      source,
      destination,
      connection,
    };
    return this.http.post<StartScanResponse>(`${this.api.baseUrl}/scan/`, payload);
  }

  /**
   * Polled on an interval while a scan is running. Matches Django's
   * scan_status view, which returns the logs dict:
   * {"token info": [...], "scan info": [...], "progressbar": int, "status": ...}
   */
  getScanStatus(scanId: string) {
    return this.http.get<ScanStatus>(`${this.api.baseUrl}/scan-status/${scanId}/`);
  }

  /**
   * Invokes BackEnd/Artifacts_Generator/DB2_2_Fabric.py to deploy the
   * scanned Tables, Views, Stored Procedures, and Volumes directly into
   * Microsoft Fabric.
   */
  generateFabricArtifacts(source?: string, filename?: string) {
    return this.http.post<{
      status: string;
      message: string;
      tables?: string[];
      errors?: string[];
      logs?: string[];
      tables_info?: any[];
      target?: any;
    }>(
      `${this.api.baseUrl}/generate-fabric-artifacts/`,
      { source, filename }
    );
  }
}
