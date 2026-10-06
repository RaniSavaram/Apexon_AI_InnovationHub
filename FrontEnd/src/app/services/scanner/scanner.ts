import { Injectable, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Api } from '../api/api';

export interface ConnectionDetails {
  server: string;
  database: string;
  username: string;
  password: string;
  /** Databricks-only: SQL warehouse endpoint, sent to Django as extra.http_path */
  httpPath?: string;
  /** Dynamics 365-only: Azure AD tenant ID, sent to Django as extra.tenant_id */
  tenantId?: string;
  /** Dynamics 365-only: app registration (client) ID, sent to Django as extra.client_id */
  clientId?: string;
  /** Dynamics 365-only: app registration client secret, sent to Django as extra.client_secret */
  clientSecret?: string;
  /** Snowflake-only: account identifier (e.g. "xy12345.us-east-1"), sent to Django as extra.account */
  account?: string;
  /** Snowflake-only: compute warehouse name, sent to Django as extra.warehouse */
  warehouse?: string;
  /** Snowflake-only: optional role to assume, sent to Django as extra.role */
  role?: string;
  /** Snowflake-only: Programmatic Access Token (PAT), sent to Django as extra.token - when set, connects without username/password */
  token?: string;
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
  extra?: {
    http_path?: string;
    tenant_id?: string;
    client_id?: string;
    client_secret?: string;
    account?: string;
    warehouse?: string;
    role?: string;
    token?: string;
  };
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
      fabric_migration_metadata?: string;
    };
  };
}

export interface ErColumn {
  name: string;
  type: string;
  nullable: boolean;
  pk: boolean;
  fk: boolean;
}

export interface ErTable {
  id: string;
  schema: string;
  name: string;
  row_count?: number | null;
  primary_key?: string | null;
  /** True when the primary key is declared in the source, not guessed. */
  primary_key_declared: boolean;
  columns: ErColumn[];
}

export interface ErRelationship {
  from: string;
  /** Comma-separated for a composite foreign key. */
  from_column: string;
  to: string;
  to_column?: string | null;
  /** Foreign key constraint name, for declared relationships. */
  name?: string | null;
  inferred: boolean;
}

export interface ErDiagramResponse {
  status: string;
  file: string;
  source?: string;
  database?: string;
  generated_at?: string;
  /** Whether the scan could read the source's declared keys at all. */
  declared_keys_read: boolean;
  /** Declared foreign keys, or (when there are none) inferred from column names. */
  relationship_source: 'declared' | 'inferred';
  tables: ErTable[];
  relationships: ErRelationship[];
}

export interface CompletedScanInfo {
  source: string;
  metadataFile?: string;
  reportFile?: string;
  planFile?: string;
  timestamp: Date;
}

@Injectable({ providedIn: 'root' })
export class Scanner {
  private http = inject(HttpClient);
  private api = inject(Api);

  readonly lastCompletedScan = signal<CompletedScanInfo | null>(null);

  setCompletedScan(info: CompletedScanInfo) {
    this.lastCompletedScan.set(info);
    try {
      sessionStorage.setItem('apexon_active_scan', JSON.stringify({
        source: info.source,
        metadataFile: info.metadataFile,
        reportFile: info.reportFile,
        planFile: info.planFile,
        timestamp: info.timestamp
      }));
    } catch {}
  }

  getCompletedScan(): CompletedScanInfo | null {
    const current = this.lastCompletedScan();
    if (current) return current;
    try {
      const raw = sessionStorage.getItem('apexon_active_scan');
      if (raw) {
        const parsed = JSON.parse(raw);
        return {
          source: parsed.source,
          metadataFile: parsed.metadataFile,
          reportFile: parsed.reportFile,
          planFile: parsed.planFile,
          timestamp: new Date(parsed.timestamp)
        };
      }
    } catch {}
    return null;
  }

  /**
   * Calls Django's connect_database view.
   * NOTE: that view reads FLAT fields off the request body
   * (request.data.get("server"), .get("database"), etc.) - not a nested
   * "connection" object - so the payload here is deliberately flat to match.
   */
  connectDatabase(source: string, connection: ConnectionDetails, rememberMe = false) {
    const extra: { http_path?: string; tenant_id?: string; client_id?: string; client_secret?: string; account?: string; warehouse?: string; role?: string; token?: string } = {};
    if (connection.httpPath) extra.http_path = connection.httpPath;
    if (connection.tenantId) extra.tenant_id = connection.tenantId;
    if (connection.clientId) extra.client_id = connection.clientId;
    if (connection.clientSecret) extra.client_secret = connection.clientSecret;
    if (connection.account) extra.account = connection.account;
    if (connection.warehouse) extra.warehouse = connection.warehouse;
    if (connection.role) extra.role = connection.role;
    if (connection.token) extra.token = connection.token;

    const payload = {
      source,
      server: connection.server,
      database: connection.database,
      username: connection.username,
      password: connection.password,
      extra,
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
   * Tables, columns, keys, and relationships for the logs dialog's
   * "ER Diagrams" tab, built by Django's er_diagram view from a finished
   * scan's Fabric metadata JSON (output_files.fabric_migration_metadata).
   * Without a file, Django falls back to the most recent scan's copy.
   */
  getErDiagram(file?: string) {
    return this.http.get<ErDiagramResponse>(`${this.api.baseUrl}/er-diagram/`, {
      params: file ? { file } : {},
    });
  }

  /**
   * Invokes the source's BackEnd/Artifacts_Generator/*2_fabric.py script
   * (databricks2_fabric.py / sqlserver2_fabric.py / dynamics3652_fabric.py)
   * to deploy the scanned Tables, Views, Stored Procedures, and Volumes
   * directly into Microsoft Fabric.
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
