# Microsoft Fabric Target Migration RAG Knowledge Base

## Purpose
Migration-focused reference for an LLM that must generate a source table catalog and table-level migration plan for Microsoft Fabric. Use this document to select the appropriate Fabric target, map schemas/types, identify transformations, and flag limitations. It is not a complete Fabric manual.

## 1. Microsoft Fabric
Microsoft Fabric is a SaaS analytics platform integrating Data Engineering, Data Factory, Data Science, Data Warehouse, Databases, Real-Time Intelligence, Power BI and related capabilities over shared platform services. **OneLake** is the centralized logical data lake for Fabric.

Migration principle: select a Fabric workload from the source workload pattern; do not map source database objects mechanically.

## 2. Fabric Architecture
Conceptual hierarchy:
`Tenant → Workspace → Fabric items → OneLake-backed data`

Common workspace items:
- Lakehouse
- Warehouse
- KQL database
- SQL Database
- Notebook
- Pipeline
- Semantic model
- Report

OneLake is tenant-wide logical storage built on ADLS Gen2 foundations. It enables shared access and zero-copy patterns through Shortcuts.

### Workspace
A workspace is a collaboration, security, lifecycle, and deployment boundary. Do not automatically map a source database/catalog to one workspace. Consider environment, domain, ownership, security, capacity, and deployment boundaries.

## 3. Target Selection
Use this decision framework:

| Source/workload characteristic | Preferred Fabric target |
|---|---|
| Delta/Spark/data engineering | Lakehouse |
| SQL-first analytical/BI workload | Warehouse |
| Event/log/time-series, low latency | Real-Time Intelligence/KQL |
| Transactional application workload | Fabric SQL Database or appropriate operational target |
| External data should remain in place | OneLake Shortcut |

Target selection must consider data types, query language, transaction requirements, streaming, BI, governance, performance, and source-retirement requirements.

## 4. OneLake
OneLake is the shared storage foundation for Fabric. It supports native data and zero-copy access through Shortcuts.

Migration choices:
- **Native copy:** target owns an independent dataset.
- **Shortcut:** target references source data without copying.
- **Mirroring:** continuous replication where supported.
- **Transform/load:** source data is transformed into target-native tables.

A Shortcut preserves a dependency on the source. Do not use it when the migration objective is complete source-platform retirement.

## 5. Lakehouse
A Fabric Lakehouse combines data-lake storage with Spark and SQL analytics. It is suited to:
- Data engineering
- Data science
- ELT
- Semi-structured data
- Medallion architectures
- Spark workloads
- Mixed engineering/analytics workloads

A Lakehouse has logical `Tables` and `Files` areas.

- `Tables`: managed Delta tables.
- `Files`: raw, semi-structured, unstructured, or file-oriented data.

A source relational/Delta table normally maps to a Lakehouse table, not Files.

## 6. Lakehouse Delta Tables
Fabric Lakehouse uses Delta Lake as its default table format. Delta provides transactional consistency, schema enforcement, schema evolution in supported scenarios, time travel/history, and reliable analytics storage.

Databricks Delta → Fabric Lakehouse Delta is generally a favorable migration pattern, but:
`Delta format compatibility != complete platform-feature compatibility`

Validate:
- Delta protocol/features
- Deletion vectors
- Change Data Feed
- Generated columns
- Identity columns
- Constraints
- Table properties
- Partitioning/clustering
- Variant
- Streaming behavior
- History/time-travel requirements

## 7. Lakehouse Schemas
Schema-enabled Lakehouses group tables into named schemas.

Fabric namespace:
`workspace.lakehouse.schema.table`

Example:
`analytics.sales.orders`

Schemas support:
- Business/domain organization
- Schema-level access control
- Cross-workspace Spark SQL references
- Schema shortcuts

Schema names can contain letters, numbers, and underscores.

The default schema for a schema-enabled Lakehouse is `dbo`.

### Databricks mapping
`Databricks catalog.schema.table`
→
`Fabric workspace.lakehouse.schema.table`

Databricks **schema → Fabric Lakehouse schema** is usually a strong mapping. Databricks **catalog → workspace/lakehouse boundary** requires architectural redesign.

Moving or renaming a Fabric schema/table requires dependent notebooks, dataflows, and SQL queries to be updated.

## 8. Lakehouse SQL Analytics Endpoint
Creating a Lakehouse generates a SQL analytics endpoint.

It:
- Provides T-SQL query access to Lakehouse Delta tables.
- Is useful for BI and read-oriented analytics.
- Can be consumed by Power BI.
- Is read-only compared with the full Warehouse SQL surface.
- Exposes Delta tables; non-Delta files such as CSV/Parquet are not automatically exposed as tables.

Do not treat the SQL analytics endpoint as equivalent to Fabric Warehouse.

## 9. Fabric Warehouse
Fabric Warehouse is SQL-first and intended for:
- T-SQL analytics
- BI/reporting
- Dimensional modeling
- Structured analytical workloads
- SQL-centric teams

Lakehouse vs Warehouse:

| | Lakehouse | Warehouse |
|---|---|---|
| Primary development | Spark | T-SQL |
| Data | Structured + semi/unstructured | Structured |
| Best for | Engineering/data science | BI/SQL analytics |
| Delta/OneLake | Native | Native |
| Multi-table transactions | Not a Lakehouse capability | Supported |
| File-oriented workloads | Strong | Not primary |

Choose Warehouse when SQL/BI and dimensional modeling dominate. Choose Lakehouse when Spark, Delta, semi-structured data, or engineering dominates.

## 10. OneLake Shortcuts
A Shortcut is a OneLake object pointing to another storage location. It behaves like a symbolic link and does not copy the target data.

Use when:
- Zero-copy access is required.
- Source remains authoritative.
- Data sharing is required.
- Copying is unnecessary.

Do not use as the final migration strategy if the source must be retired.

Shortcuts can point to supported OneLake and external storage sources such as ADLS Gen2 and Amazon S3. Delta table shortcuts can be queried as tables in supported Lakehouse scenarios.

## 11. Schema Shortcuts
Schema shortcuts can expose multiple Delta tables from another Fabric Lakehouse or supported external storage as a schema.

Useful for:
- Large groups of Delta tables
- Zero-copy consumption
- Shared data domains

Risk: schema shortcuts preserve a source-storage dependency.

## 12. Fabric Data Types
Target type mapping depends on the Fabric engine.

### Lakehouse/Spark
Common types:
- BOOLEAN
- TINYINT/BYTE
- SMALLINT/SHORT
- INT
- BIGINT/LONG
- FLOAT
- DOUBLE
- DECIMAL(p,s)
- STRING
- BINARY
- DATE
- TIMESTAMP
- ARRAY
- MAP
- STRUCT
- VARIANT where supported

### Warehouse/T-SQL
Common types:
- bit
- tinyint
- smallint
- int
- bigint
- decimal/numeric
- float/real
- char/varchar
- date
- datetime2
- time
- binary/varbinary
- uniqueidentifier

Rule:
`source type + target engine + workload = target type`

Never use one global mapping for all Fabric targets.

## 13. Numeric Types
Preserve:
- Precision
- Scale
- Overflow behavior
- Rounding behavior

For example, do not silently convert `DECIMAL(18,2)` to a lower-precision type.

Financial/scientific numeric columns require explicit validation.

## 14. Complex Types
### ARRAY
Preserve when supported and useful. Otherwise consider normalization or serialization only after checking downstream consumers.

### MAP
Assess key lookup/query patterns and target support.

### STRUCT
Preserve nested named fields when supported. Do not flatten automatically.

### VARIANT
Validate Fabric Spark runtime, Delta protocol, SQL accessibility, and BI compatibility before migrating as-is.

## 15. SQL Surfaces
Fabric has multiple SQL/programming surfaces:
- Spark SQL for Data Engineering/Spark.
- T-SQL through the Lakehouse SQL analytics endpoint.
- T-SQL in Fabric Warehouse.
- KQL in KQL databases/Real-Time Intelligence.

Classify source SQL before rewriting:
`Source SQL → target engine → target dialect`

Do not assume Databricks SQL, Spark SQL, Lakehouse T-SQL, and Warehouse T-SQL are interchangeable.

## 16. SQL Migration Risk
### Low
SELECT, basic joins, filters, aggregations, CASE, common windows.

### Medium
Complex date/time logic, JSON functions, advanced windows, MERGE, dynamic SQL, cross-workspace references, vendor-specific functions.

### High
Source-specific procedural SQL, proprietary functions, Delta maintenance commands, streaming SQL, source-specific external-table/security syntax.

## 17. Tables
For every target table capture:
- Workspace
- Lakehouse/Warehouse
- Schema
- Table
- Source object
- Target object type
- Columns
- Target data types
- Nullability
- Precision/scale
- Description
- Storage strategy
- Partitioning/layout
- Dependencies
- Governance
- Migration risk

## 18. Views
A migrated view requires:
1. Source definition extraction.
2. Dependency extraction.
3. Target SQL-surface selection.
4. SQL rewrite.
5. Result validation.

Never migrate a view by name alone.

## 19. Keys and Constraints
Separate business semantics from physical implementation.

For source primary/foreign/unique/check constraints determine:
- Whether target supports the constraint.
- Whether the constraint is enforced.
- Whether the business rule must be preserved elsewhere.

If a key drives MERGE, CDC, joins, or uniqueness, preserve its semantic role even if implementation changes.

## 20. Data Factory and Pipelines
Fabric Data Factory provides ingestion, copy, transformation and orchestration capabilities.

Pipelines are appropriate for:
- Data movement
- Scheduling
- Dependencies
- Orchestration
- Copy activities

A source job/workflow is not a table. Migrate it as a separate workload layer using Pipeline, Notebook, Dataflow, Spark job, or a combination.

## 21. Notebooks and Spark
Fabric Data Engineering provides Apache Spark notebooks/jobs.

Databricks notebook migration must identify:
- Standard Spark/Python/SQL code
- Databricks-specific APIs
- DBFS paths
- Databricks utilities
- Secrets
- Runtime-specific libraries
- Streaming code

Standard Spark code is generally easier to migrate. Platform-specific code requires replacement.

## 22. Medallion Architecture
Common logical architecture:
`Bronze → Silver → Gold`

Bronze = raw/minimally processed.
Silver = cleaned/standardized/validated.
Gold = curated business-ready.

Preserve a source medallion architecture when it improves reuse, governance, quality, and incremental processing. Do not create three layers automatically for every database.

## 23. Real-Time Intelligence and KQL
Real-Time Intelligence is intended for data-in-motion scenarios.

Relevant components:
- Real-Time hub
- Event streams
- KQL databases
- Event ingestion
- Real-time analytics

KQL is a strong candidate for:
- Logs
- IoT telemetry
- Clickstreams
- Security events
- Time-series/event analytics

Do not force low-latency event workloads into a batch Lakehouse when real-time requirements dominate.

## 24. Mirroring
Fabric Mirroring can continuously replicate supported source data into OneLake.

Use when:
- Source remains operational.
- Continuous/near-real-time replication is required.
- Source is supported.
- Replication is preferable to custom ETL.

Mirroring is a replication strategy, not a universal replacement for transformations or application migration.

## 25. Governance and Security
Fabric governance spans:
- Workspace roles
- Item permissions
- OneLake security
- OneLake Catalog
- Microsoft Purview capabilities
- Sensitivity labels
- Lineage
- Auditing
- Row-level security
- Column-level security where supported

Migrate security as **policy intent**, not as a literal copy of source permissions.

Capture:
`who → what data → granularity → restriction → purpose`

Then implement the equivalent Fabric policy.

## 26. Workspace Roles
Common workspace roles:
- Admin
- Member
- Contributor
- Viewer

Do not map source database users directly to workspace roles. Separate:
- Platform administration
- Development
- Data ownership
- Data consumption

## 27. Performance and Physical Design
Do not copy source tuning blindly.

For each source optimization ask:
1. Why was it introduced?
2. What workload does it improve?
3. Does the same issue exist in Fabric?
4. What is the Fabric-native solution?

Consider:
- File sizes
- Data layout
- Partitioning
- Query patterns
- Spark behavior
- Warehouse design
- Capacity/concurrency
- Maintenance

## 28. Partitioning
Partitioning may improve pruning but can cause too many partitions, small files, slower writes, and metadata overhead.

Evaluate:
- Cardinality
- Query predicates
- Volume
- Incremental loads
- File sizes

Do not preserve source partitioning automatically.

## 29. Delta Optimization
Treat source `OPTIMIZE`, `ZORDER`, clustering, or similar features as **optimization intent**, not literal migration commands.

Reassess physical design for Fabric.

## 30. Time Travel and History
Copying the current table does not necessarily migrate historical Delta versions.

If source history is needed for:
- Audit
- Rollback
- Reproducibility
- Compliance
- Historical analysis

define an explicit history migration/retention strategy.

## 31. CDC and Incremental Loads
Identify:
- Source CDC mechanism
- Change key
- Ordering
- Delete handling
- Late-arriving data
- Deduplication
- Target merge/upsert logic
- Restart/recovery behavior

Possible Fabric implementations include Pipelines, connectors, Spark transformations, MERGE patterns, Real-Time Intelligence, and Mirroring where supported.

## 32. Databricks → Fabric Mapping
| Databricks | Fabric | Guidance |
|---|---|---|
| Catalog | Workspace/Lakehouse boundary | Redesign |
| Schema | Lakehouse schema | Strong mapping |
| Delta table | Lakehouse Delta table | Validate features |
| Managed table | Native Lakehouse table | Strong mapping |
| External table | Native table or Shortcut | Copy vs reference decision |
| View | Lakehouse/warehouse view | Rewrite SQL |
| Volume | Lakehouse Files/Shortcut | Depends on content |
| SQL Warehouse | Fabric Warehouse/SQL endpoint | Workload dependent |
| Notebook | Fabric Notebook | Validate APIs/libraries |
| Job | Fabric Pipeline/Notebook orchestration | Rebuild workflow |
| Auto Loader | Fabric ingestion/streaming | Rebuild |
| Structured Streaming | Fabric Spark/Real-Time | Architecture decision |
| Lakeflow pipeline | Pipeline + Notebook/Dataflow | Rebuild |
| Unity Catalog | Fabric governance | Policy redesign |
| External location | Shortcut/storage integration | Validate security |
| ZORDER | Fabric physical optimization | Redesign |
| Liquid clustering | Fabric physical optimization | Redesign |
| OPTIMIZE | Fabric Delta optimization | Translate intent |
| VACUUM | Target maintenance | Translate retention intent |
| CDF | Incremental/CDC strategy | Validate |
| Identity column | Target key strategy | Explicit decision |
| Generated column | Target generated/derived logic | Validate |
| Time travel | Target Delta history | Validate retention |

## 33. Databricks Delta → Fabric Lakehouse
Preferred conceptual mapping for Delta/Spark workloads:

`Databricks catalog.schema.table → Fabric workspace.lakehouse.schema.table`

Use when:
- Source is Delta.
- Spark/data engineering is important.
- Target needs Lakehouse semantics.
- Unsupported Databricks-specific dependencies are limited.

## 34. Databricks SQL → Fabric Warehouse
For SQL-first workloads:
`Databricks catalog.schema.table → Fabric Warehouse schema.table`

Potential work:
- Type conversion
- SQL rewrite
- View conversion
- Physical design
- Keys/constraints
- ETL conversion

Do not use this target automatically for Spark-heavy or complex nested-data workloads.

## 35. Target Table Catalog
Recommended target catalog fields:
- Source system
- Source database/catalog
- Source schema
- Source table
- Target workspace
- Target Lakehouse/Warehouse
- Target schema
- Target table
- Target object type
- Source/target types
- Transformations
- Storage strategy
- Partition/layout
- Workload
- Dependencies
- Governance
- Risk
- Validation

## 36. Target Migration Plan
For each object provide:
- Target architecture
- Target object
- Migration strategy
- Schema conversion
- SQL conversion
- Data movement
- Incremental/CDC strategy
- Physical design
- Governance
- Dependencies
- Validation
- Risk
- Confidence
- Manual actions

## 37. Target Selection Rules
1. Delta + Spark/data engineering → prefer Lakehouse.
2. SQL-first relational analytics → evaluate Warehouse.
3. Event/time-series low-latency → evaluate Real-Time Intelligence/KQL.
4. Transactional application → evaluate Fabric SQL Database/operational target.
5. Zero-copy external access → evaluate OneLake Shortcut.
6. Source must be retired → avoid source-dependent Shortcut as final state.
7. Target needs independent ownership → native Fabric table.
8. Complex Spark transformations → Lakehouse.
9. Strong dimensional BI/SQL requirement → Warehouse.
10. Missing metadata → flag uncertainty rather than inventing architecture.

## 38. Migration Risk
### Low
Simple Delta/relational table, primitive types, no special features, simple SQL, no streaming/external dependency.

### Medium
Views, complex SQL, nested types, external tables, CDC, advanced Delta features.

### High
Streaming, complex CDC, Databricks-specific code, complex governance, source-specific storage/security, complex orchestration.

### Critical/Unknown
Missing metadata, unknown dependencies, unsupported target feature, proprietary source behavior.

## 39. Validation
### Schema
Compare table names, schemas, columns, types, precision/scale, nullability, nested structures and relevant constraints.

### Data
Compare row counts, distinct keys, null counts, min/max, sums/aggregates, representative records and hashes where appropriate.

### Functional
Validate views, queries, MERGE/upserts, CDC, incremental loads, streaming and BI outputs.

### Performance
Compare query latency, load/refresh time, streaming latency, concurrency and resource behavior.

### Security
Verify workspace, schema/table, row/column, sensitivity and sharing behavior.

## 40. Common Mistakes
- Mapping every source table to Warehouse.
- Treating SQL analytics endpoint as Warehouse.
- Treating Delta compatibility as full feature compatibility.
- Using Shortcuts when source retirement is required.
- Copying source partitions/clustering blindly.
- Migrating notebooks without replacing platform-specific APIs.
- Flattening complex types without checking consumers.
- Ignoring workspace architecture.
- Ignoring source history.
- Treating security as simple permission copying.
- Migrating tables without pipelines/jobs/dependency analysis.

## 41. RAG Retrieval Rules
When answering a migration question:
1. Identify the Fabric workload first.
2. Distinguish Lakehouse, Warehouse, KQL, SQL Database and Shortcut.
3. Prefer Lakehouse for Delta/Spark/data engineering.
4. Evaluate Warehouse for SQL-first BI.
5. Evaluate KQL for event/time-series workloads.
6. Use Shortcuts only when source dependency is acceptable.
7. Validate actual target-engine type support.
8. Preserve DECIMAL precision/scale.
9. Inspect nested types recursively.
10. Treat workspace design as an architectural decision.
11. Treat governance as policy mapping.
12. Treat source optimization as redesign intent.
13. Treat streaming and CDC as workload behavior.
14. Convert SQL according to target surface.
15. Separate table migration from pipeline/job/notebook migration.
16. Report unsupported or uncertain capabilities explicitly.
17. Lower confidence when metadata is incomplete.
18. Prefer target-native Fabric patterns over source-specific emulation.

## 42. Canonical Target Recommendation
```text
SOURCE:
<source system>.<database/catalog>.<schema>.<object>

FABRIC TARGET:
<workspace>.<lakehouse/warehouse>.<schema>.<object>

TARGET WORKLOAD:
<Lakehouse | Warehouse | KQL | SQL Database | Shortcut>

TARGET OBJECT:
<Table | View | Shortcut | Other>

STORAGE:
<Native Delta | Shortcut | Warehouse | Other>

SCHEMA MAPPING:
<source → target types>

DATA MOVEMENT:
<Full | Incremental | CDC | Streaming | Shortcut | Mirroring>

SQL CHANGES:
<required rewrites>

PHYSICAL DESIGN:
<partition/layout/warehouse design>

GOVERNANCE:
<security mapping>

DEPENDENCIES:
<views/pipelines/notebooks/BI/streaming>

VALIDATION:
<schema/data/functional/performance/security>

RISK:
<Low | Medium | High | Critical/Unknown>

CONFIDENCE:
<High | Medium | Low>

MANUAL ACTIONS:
<human decisions>
```

## 43. Minimum Metadata for Reliable Target Selection
Ideally know:
- Source platform
- Object type
- Table format
- Row count/size
- Columns/types
- Nested types
- Read/write pattern
- SQL/Spark workload
- BI consumers
- Streaming/CDC
- Latency requirement
- Governance/security
- Sharing requirements
- Storage location
- Dependencies
- Source-retirement requirement

If critical information is missing, state `Target selection requires additional assessment`.

## 44. Compact Feature Matrix
| Fabric capability | Role | Migration significance |
|---|---|---|
| OneLake | Unified storage | Primary storage foundation |
| Workspace | Security/lifecycle boundary | Architecture decision |
| Lakehouse | Engineering/lake analytics | Strong Delta/Spark target |
| Lakehouse schema | Table organization | Strong source-schema mapping |
| Delta table | Lakehouse format | Strong Databricks target |
| SQL analytics endpoint | SQL access to Lakehouse | Read-oriented |
| Warehouse | SQL analytics/BI | SQL-first target |
| Shortcut | Zero-copy reference | Preserves source dependency |
| Notebook | Spark development | Notebook migration target |
| Pipeline | Orchestration | Job/workflow target |
| Data Factory | Integration | Ingestion/ETL target |
| Real-Time hub | Streaming | Event workloads |
| KQL database | Event/time-series | Real-time target |
| Power BI | BI | Downstream consumer |
| OneLake Catalog | Discovery/governance | Metadata layer |
| Purview | Governance/compliance | Policy migration |
| Mirroring | Replication | Continuous replication option |

## 45. Final Principles
- OneLake is the central Fabric storage foundation.
- Lakehouse is the primary target for Delta/Spark/data-engineering workloads.
- Warehouse is the primary target for SQL-first analytical workloads.
- KQL/Real-Time Intelligence is appropriate for event/time-series workloads.
- Lakehouse SQL analytics endpoint is not equivalent to Warehouse.
- Source schema commonly maps to Lakehouse schema.
- Source catalog/database should not automatically become a workspace.
- Workspace design should reflect security, ownership, lifecycle and capacity.
- Delta-to-Delta is favorable but feature compatibility must be checked.
- Shortcuts provide zero-copy access but preserve source dependencies.
- Native Delta is preferable when Fabric should own the migrated data.
- Source physical optimizations should be redesigned.
- Streaming and CDC require behavioral/architectural migration.
- SQL must be mapped to the selected Fabric SQL surface.
- Data types must be mapped per target engine.
- Governance must be migrated as policy intent.
- Current data and historical data are separate requirements.
- Table migration and workload migration are separate but related.
- Unknown compatibility must be reported, not invented.

## Official Microsoft Learn Sources
- Fabric overview: https://learn.microsoft.com/en-us/fabric/fundamentals/microsoft-fabric-overview
- Fabric documentation: https://learn.microsoft.com/en-us/fabric/
- Lakehouse overview: https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-overview
- Lakehouse and Delta tables: https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-and-delta-tables
- Lakehouse schemas: https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-schemas
- Create a Lakehouse: https://learn.microsoft.com/en-us/fabric/data-engineering/create-lakehouse
- OneLake: https://learn.microsoft.com/en-us/fabric/onelake/
- OneLake Shortcuts: https://learn.microsoft.com/en-us/fabric/onelake/onelake-shortcuts
- Create OneLake Shortcut: https://learn.microsoft.com/en-us/fabric/onelake/shortcuts/create-onelake-shortcut

Validate version-sensitive behavior against current Microsoft Learn documentation and the target Fabric tenant before executing migration.
