# 1. Databricks Platform Overview

Databricks is a cloud data and AI platform built around a lakehouse architecture. The platform combines data engineering, SQL analytics, BI, machine learning, AI, governance, and data applications over a shared data foundation.

For migration purposes, the important architectural characteristics are:

- Lakehouse storage rather than a traditional database-only storage model.
- Cloud object storage as the underlying storage layer.
- Delta Lake as the primary table format in Databricks workloads.
- Apache Spark as a major processing engine.
- Databricks SQL as the SQL analytics/query layer.
- Unity Catalog as the centralized governance and metadata layer.
- Lakeflow for ingestion, transformation, and orchestration.
- Support for batch and streaming workloads.
- Support for managed and external tables.
- Support for open formats and interoperability with external systems.

Databricks reference architectures organize workloads into the following conceptual stages:

`Source → Ingest → Transform → Query/Process → Serve → Analysis → Storage`

External data can be brought into Databricks through ETL, Lakehouse Federation, or managed ingestion connectors. Lakehouse Federation can expose external SQL data through Unity Catalog without first copying it into object storage.

## Migration implication

Do not treat a Databricks environment as only a collection of database tables.

A migration inventory should distinguish:

- Data objects.
- Physical storage.
- Table format.
- SQL logic.
- Spark/Databricks code.
- Streaming pipelines.
- Jobs and orchestration.
- Governance and permissions.
- Performance optimizations.
- External dependencies.

A table may appear simple at the catalog level but depend on Delta-specific features, external locations, generated columns, row filters, clustering, streaming pipelines, or downstream views.

---

# 2. Databricks Lakehouse Architecture

## 2.1 Logical architecture

A Databricks lakehouse combines:

- Cloud object storage.
- Delta Lake tables.
- Spark processing.
- SQL analytics.
- Governance through Unity Catalog.
- Data engineering pipelines.
- BI and AI workloads.

The lakehouse supports structured, semi-structured, and unstructured data.

A typical data engineering flow is:

`Source systems → Ingestion → Bronze → Silver → Gold → SQL/BI/AI consumers`

The Bronze/Silver/Gold pattern is a common medallion architecture:

- **Bronze**: raw or minimally processed data.
- **Silver**: cleaned, standardized, validated data.
- **Gold**: curated business-ready datasets and aggregates.

These are logical workload layers, not mandatory Databricks object types.

## 2.2 Storage

Databricks tables are commonly backed by cloud object storage.

Delta Lake adds transaction-log metadata and table-management capabilities on top of data files.

Most Unity Catalog tables are Delta tables, although Databricks can also work with other table/file formats.

Migration assessment must therefore identify:

- Table format.
- Managed vs external.
- Physical location.
- Partitioning.
- Clustering.
- Delta table features.
- Table history/time-travel requirements.
- Dependencies on cloud storage.

## 2.3 Compute and processing

Databricks workloads can run using:

- SQL warehouses.
- Serverless compute.
- Job compute.
- Interactive compute.
- Spark-based processing.

Spark is used extensively for batch and streaming transformations.

Databricks also provides Photon, an optimized execution engine for supported workloads.

### Migration rule

Compute configuration is normally **not migrated as data**.

Record compute settings only when they affect:

- Performance expectations.
- Cost assumptions.
- Runtime compatibility.
- Spark behavior.
- Streaming latency.
- Required libraries.
- Workload sizing.

The target Fabric implementation should redesign compute rather than mechanically reproduce Databricks cluster settings.

---

# 3. Unity Catalog Object Model

## 3.1 Three-level namespace

Unity Catalog uses:

`catalog.schema.object`

For tables:

`catalog.schema.table`

The hierarchy is:

`Metastore → Catalog → Schema → Table/View/Volume/Function`

A catalog is the highest data-organization level inside the Unity Catalog metastore.

A schema is a child of a catalog and can contain:

- Tables.
- Views.
- Volumes.
- Functions.
- Models and other governed assets.

Databricks sometimes uses the term **database** for a schema. In Unity Catalog, `CREATE DATABASE` is an alias for `CREATE SCHEMA`.

## 3.2 Catalog

A catalog is a logical unit of data organization and governance.

Typical reasons to create separate catalogs include:

- Data isolation.
- Business/domain separation.
- Environment separation.
- Governance boundaries.
- Access-control boundaries.

Catalog privileges can inherit to contained schemas and objects.

### Migration consideration

A Databricks catalog does not have a guaranteed one-to-one Microsoft Fabric equivalent.

Do not automatically map:

`Databricks catalog → Fabric schema`

Instead evaluate whether the catalog represents:

- An environment.
- A business domain.
- A security boundary.
- A logical data product.
- A physical storage boundary.

For Fabric Lakehouse targets, the target architecture may use:

`Fabric workspace → Lakehouse → schema → table`

or multiple workspaces/lakehouses depending on governance and isolation requirements.

## 3.3 Schema

In Unity Catalog:

`catalog.schema`

A schema groups tables, views, volumes, functions, and related assets.

Schemas are useful for:

- Logical organization.
- Domain organization.
- Team/project organization.
- Access control.
- Data discovery.

`INFORMATION_SCHEMA` is a reserved, system-provided schema in Unity Catalog.

### Migration mapping

Databricks schema → Fabric Lakehouse schema is generally a strong conceptual mapping when the target is a schema-enabled Fabric Lakehouse.

Fabric Lakehouse schemas group tables into named collections and can be used for domain organization and schema-level access control.

## 3.4 Table

A table is a persistent relation containing structured data.

A Databricks table may be:

- Managed.
- External.
- Delta.
- Apache Iceberg.
- Other supported data-source formats.
- Streaming table.
- Materialized view-related object depending on the workload.

Most Unity Catalog tables are Delta-backed.

## 3.5 View

A Databricks view is a read-only object defined by a query over one or more tables/views.

Views can reference objects across schemas and catalogs subject to permissions.

### Migration consideration

A view migration requires:

1. Extract view definition.
2. Identify referenced tables.
3. Parse Databricks/Spark SQL.
4. Check functions and syntax.
5. Check cross-schema/cross-catalog references.
6. Convert the SQL to the target Fabric SQL/Spark SQL dialect.
7. Validate result equivalence.

Do not classify views as simple metadata-only migrations.

## 3.6 Volumes

Unity Catalog volumes provide governed storage for files that are not necessarily relational tables.

Examples include:

- Raw files.
- CSV.
- JSON.
- Images.
- Documents.
- Other unstructured/semi-structured assets.

### Migration consideration

A volume is not equivalent to a table.

Assess whether the content should become:

- Fabric Lakehouse `Files`.
- Fabric Lakehouse `Tables`.
- OneLake Shortcut.
- External storage reference.
- Another target service.

---

# 4. Databricks Table Types

## 4.1 Managed tables

A managed table is controlled by the Databricks/Unity Catalog environment, including its storage location.

For migration, capture:

- Catalog.
- Schema.
- Table.
- Format.
- Location if available.
- Columns.
- Table properties.
- Partitioning/clustering.
- Constraints.
- Generated columns.
- Comments.
- Tags.
- Row filters/masks.
- History requirements.

## 4.2 External tables

External tables reference data stored at a specified external location.

Dropping an external table does not necessarily delete the underlying files.

### Migration risk

External tables may depend on:

- Cloud storage paths.
- Cloud credentials.
- External locations.
- Storage permissions.
- Directory structures.
- External table metadata.

These dependencies must be explicitly assessed.

## 4.3 Delta tables

Delta Lake is the primary table format to identify in Databricks.

Delta provides capabilities including:

- ACID transactions.
- Schema enforcement.
- Schema evolution.
- Time travel/history.
- MERGE.
- UPDATE.
- DELETE.
- Change Data Feed where enabled.
- Constraints.
- Generated columns.
- Deletion vectors.
- Table optimization.
- Data-layout management.

### Migration rule

Do not treat every Delta feature as automatically portable merely because Microsoft Fabric also uses Delta Lake.

The **table format may be compatible while the platform-specific behavior is not**.

Assess Delta protocol level and enabled table features.

## 4.4 Streaming tables

A Databricks streaming table is a Delta table with additional support for streaming/incremental processing.

Streaming tables are tied to Lakeflow pipeline behavior and are not equivalent to an ordinary static table.

### Migration rule

If a source table is a streaming table, migration planning must include:

- Source stream.
- Trigger behavior.
- State.
- Checkpointing.
- Incremental logic.
- Data quality expectations.
- Schema evolution.
- CDC/apply-changes logic.
- Target streaming architecture.

Do not migrate only the current table contents and declare the workload migrated.

---

# 5. Table Metadata Required for Migration

The source table catalog should capture at least:

## Identity

- Catalog name.
- Schema/database name.
- Table name.
- Fully qualified table name.
- Table type.
- Table format.

## Description

- Table comment/description.
- Column comments.
- Business meaning where available.

## Schema

For every column:

- Column name.
- Data type.
- Nested data type definition.
- Nullable.
- Default expression.
- Generated expression.
- Identity property.
- Masking.
- Constraints.
- Comment.

## Physical/storage metadata

- Managed/external.
- Storage location.
- File format.
- Partition columns.
- Clustering columns.
- Table properties.
- Delta protocol/features.
- Deletion vectors.
- Change Data Feed if enabled.
- Table history/time-travel requirements.

## Governance

- Owner.
- Catalog/schema permissions.
- Table permissions.
- Row filters.
- Column masks.
- Tags.
- Lineage.
- Sensitive-data classification if available.

## Workload metadata

- Read/write frequency.
- Batch vs streaming.
- Upstream dependencies.
- Downstream dependencies.
- Views depending on the table.
- Jobs/pipelines using the table.
- SQL queries using the table.
- BI/ML consumers.

---

# 6. Databricks Data Types

Databricks SQL supports primitive and complex types.

## 6.1 Common primitive types

| Databricks type | Migration assessment |
|---|---|
| BOOLEAN | Usually direct for Fabric Spark/Lakehouse |
| TINYINT | Validate target Spark/SQL representation |
| SMALLINT | Validate target Spark/SQL representation |
| INT / INTEGER | Usually direct for Fabric Spark/Lakehouse |
| BIGINT | Usually direct |
| FLOAT | Usually direct |
| DOUBLE | Usually direct |
| DECIMAL(p,s) | Direct conceptually; preserve precision and scale |
| STRING | Usually direct |
| VARCHAR(n) | Validate length semantics and target engine |
| CHAR(n) | Validate fixed-length semantics |
| BINARY | Validate target engine/client behavior |
| DATE | Usually direct |
| TIMESTAMP | Validate timestamp/time-zone semantics |
| TIMESTAMP_NTZ | Validate target support and semantics |
| INTERVAL | Requires validation |
| VARIANT | Validate target runtime/version |
| VOID | Do not assume target support |

## 6.2 DECIMAL

Databricks DECIMAL supports precision from 1 through 38.

Migration must preserve:

- Precision.
- Scale.
- Arithmetic behavior.
- Overflow behavior.

Never reduce precision automatically.

## 6.3 ARRAY

`ARRAY<T>` stores an ordered collection of values.

Migration questions:

- Does the target engine support arrays natively?
- Are nested arrays present?
- Are array functions used?
- Is the array queried by SQL?
- Is downstream BI expecting flattened columns?

If the target layer cannot preserve the type, choose between:

- JSON/string representation.
- Normalized child table.
- Target-native complex type.

The decision must be workload-driven.

## 6.4 MAP

`MAP<K,V>` stores key/value pairs.

Databricks map keys must be unique and non-null.

Migration questions:

- Are map keys queried individually?
- Is the map nested?
- Is the target SQL engine able to query the structure?
- Would normalization be more appropriate?

## 6.5 STRUCT

`STRUCT` represents nested named fields.

Migration assessment must inspect the complete recursive structure.

Example:

`STRUCT<customer_id:BIGINT,address:STRUCT<city:STRING,zip:STRING>>`

Do not reduce nested structures to `STRING` without checking downstream usage.

## 6.6 VARIANT

Databricks `VARIANT` represents semi-structured data.

It can represent JSON-like objects, arrays, and scalar values.

Migration requires special validation because support depends on the target engine/runtime and Delta protocol capabilities.

Microsoft Fabric also supports Variant for Delta tables in supported Fabric Spark runtime versions, so a Databricks VARIANT column may be portable when the required Fabric runtime and Delta compatibility are available.

## 6.7 Type migration rule

For every non-trivial type:

`Source type → Target type → Conversion required? → Semantic risk → Validation`

Do not use name matching alone.

---

# 7. SQL and DDL Features

Databricks SQL is based on Spark SQL with Databricks-specific extensions.

The SQL migration assessment should identify:

- DDL.
- DML.
- Functions.
- Window functions.
- Aggregations.
- Date/time functions.
- JSON functions.
- Array/map/struct functions.
- MERGE.
- Delta commands.
- Databricks-specific syntax.
- Lakeflow pipeline SQL.

## 7.1 CREATE TABLE

Databricks supports table creation using column definitions, queries, locations, and data-source formats.

Important clauses can include:

- `USING`
- `LOCATION`
- `PARTITIONED BY`
- `CLUSTER BY`
- `TBLPROPERTIES`
- `COMMENT`
- `GENERATED`
- `IDENTITY`
- `DEFAULT`
- `MASK`
- Row filters
- Constraints

### Migration rule

A CREATE TABLE statement must be parsed into metadata rather than copied blindly.

Extract:

- Column definitions.
- Types.
- Nullability.
- Defaults.
- Generated expressions.
- Identity.
- Partitioning.
- Clustering.
- Location.
- Properties.
- Constraints.

Then generate a target-specific DDL plan.

## 7.2 MERGE

Databricks `MERGE INTO` supports updates, inserts, and deletes based on matching source and target rows.

It is primarily associated with Delta Lake tables.

Common migration use cases:

- Upserts.
- CDC application.
- SCD Type 1.
- SCD Type 2.
- Synchronization.

### Migration rule

A table using MERGE is not simply a static table migration.

The migration plan should preserve:

- Match condition.
- Insert behavior.
- Update behavior.
- Delete behavior.
- Source-to-target column mapping.
- Schema evolution behavior.
- Transaction semantics.

## 7.3 INSERT / UPDATE / DELETE

Standard DML is generally easier to translate, but the exact target syntax and transactional behavior must still be validated.

## 7.4 OPTIMIZE

`OPTIMIZE` rewrites data files to improve layout.

It can perform:

- File compaction.
- Partition-level optimization.
- Z-Ordering for applicable Delta tables.
- Liquid-clustering-related optimization.

This is a **physical optimization operation**, not business logic.

### Migration rule

Do not migrate `OPTIMIZE` statements literally.

Instead:

1. Identify why optimization exists.
2. Identify the columns involved.
3. Identify whether the workload is read-heavy or write-heavy.
4. Apply the appropriate Fabric/Delta optimization strategy.
5. Validate performance.

## 7.5 VACUUM

`VACUUM` removes obsolete/unreferenced data files older than the configured retention threshold.

The default Delta retention threshold documented by Databricks is 7 days.

### Migration rule

`VACUUM` is operational maintenance, not table transformation.

Record:

- Retention requirement.
- Time-travel requirement.
- Compliance implications.
- Storage-cost motivation.

Then implement the target platform's equivalent maintenance strategy.

## 7.6 SHOW/DESCRIBE commands

Useful source discovery commands include:

- `SHOW CATALOGS`
- `SHOW SCHEMAS`
- `SHOW TABLES`
- `SHOW VIEWS`
- `DESCRIBE SCHEMA`
- `DESCRIBE TABLE`
- `SHOW CREATE TABLE`

`INFORMATION_SCHEMA` provides metadata views for catalogs, schemas, tables, views, columns, and related objects.

For a migration tool, metadata extraction should prefer structured catalog metadata over parsing screenshots or notebook output.

---

# 8. Delta Lake Features With Migration Impact

## 8.1 ACID transactions

Delta provides transactional consistency.

Migration must preserve transactional behavior when the workload depends on:

- Concurrent writes.
- MERGE.
- UPDATE/DELETE.
- CDC.
- Atomic table replacement.

## 8.2 Schema enforcement

Delta validates data against the table schema on write.

Migration should preserve:

- Column types.
- Nullability.
- Required columns.
- Constraints.
- Evolution behavior.

## 8.3 Schema evolution

Databricks supports schema evolution in applicable Delta and pipeline scenarios.

Source workloads may automatically add columns or evolve schemas.

### Migration rule

If schema evolution is enabled, the migration plan must specify:

- Allowed changes.
- Detection mechanism.
- Target schema-update behavior.
- Validation.
- Compatibility policy.

## 8.4 Time travel

Delta tables can maintain table history and support querying historical versions subject to retention and table configuration.

Migration must determine whether users or applications depend on:

- Historical snapshots.
- Auditing.
- Rollback.
- Reproducibility.

A simple data copy may not preserve the historical version history.

## 8.5 Change Data Feed

If CDF is used, identify:

- Consumers.
- Retention.
- Change-event semantics.
- Downstream incremental processing.

Do not assume CDF behavior is identical across platforms.

## 8.6 Deletion vectors

Deletion vectors can accelerate `DELETE`, `UPDATE`, and `MERGE` by recording row-level changes in metadata rather than rewriting complete Parquet files.

They are a **table-feature compatibility concern**.

Migration should detect whether deletion vectors are enabled and determine whether the target supports the required Delta protocol/features.

## 8.7 Generated columns

Generated columns automatically compute values from expressions over other columns.

Important limitation:

- Enabling generated columns upgrades the table writer protocol.
- This can affect compatibility with external Delta clients.

Migration must extract:

- Generated column name.
- Data type.
- Generation expression.
- Dependencies.

## 8.8 Identity columns

Databricks identity columns generate unique values.

Important limitations include:

- Identity columns are BIGINT.
- Concurrent transactions are not supported on tables with identity columns.
- Identity columns cannot be partition columns.
- Existing identity values cannot be updated directly.

### Migration rule

Do not recreate identity semantics automatically.

Determine whether the target should use:

- Identity/autogenerated keys.
- Existing source keys preserved as ordinary columns.
- A sequence/key-generation mechanism.
- Application-generated IDs.

Preserving existing IDs is often required for referential integrity.

---

# 9. Partitioning and Data Layout

Databricks workloads may use:

- Traditional partitioning.
- Z-Ordering.
- Liquid clustering.
- File compaction.
- Dynamic file pruning.
- Predictive optimization.

## 9.1 Traditional partitioning

Partitioning physically organizes data by partition columns.

Migration assessment should identify:

- Partition columns.
- Partition cardinality.
- Number of partitions.
- Small-file problems.
- Query predicates using partition columns.

Do not copy partitioning blindly.

A poor source partition strategy can be a performance problem rather than a feature to preserve.

## 9.2 Z-Ordering

Z-Ordering organizes data to improve data skipping for common query predicates.

Treat it as a source performance optimization.

Do not create a migration requirement merely because `ZORDER BY` exists.

Instead capture:

- Z-Ordered columns.
- Query patterns motivating the optimization.
- Expected Fabric optimization equivalent.

## 9.3 Liquid clustering

Liquid clustering is a Databricks data-layout technique that can replace traditional partitioning and Z-Ordering.

It allows clustering keys to evolve without rewriting all existing data solely to change partitioning.

### Migration rule

Liquid clustering is not a logical schema feature.

Record it as:

`source physical optimization → target physical optimization`

and redesign based on Fabric workload characteristics.

---

# 10. Streaming and Incremental Processing

Databricks supports streaming workloads through Spark Structured Streaming and Lakeflow/Spark Declarative Pipelines.

Relevant components include:

- Auto Loader.
- Structured Streaming.
- Streaming tables.
- Change data processing.
- Lakeflow Connect.
- Spark Declarative Pipelines.

## 10.1 Auto Loader

Auto Loader incrementally processes files arriving in cloud object storage.

It supports:

- Incremental file discovery.
- Schema inference.
- Schema evolution.
- High-volume ingestion.

### Migration rule

Auto Loader itself is not a target table feature.

Identify:

- Source storage.
- File arrival pattern.
- Incremental state.
- Schema evolution.
- Checkpoint/state behavior.
- Transformation logic.

Then redesign ingestion in Fabric.

## 10.2 Structured Streaming

Structured Streaming provides Spark-based streaming processing.

When combined with Delta, Databricks documentation describes support for low-latency processing, exactly-once semantics, and ACID behavior.

### Migration rule

A streaming workload requires architecture migration, not just data migration.

Capture:

- Trigger interval/mode.
- Source connector.
- Checkpoint.
- Watermark.
- Stateful operations.
- Deduplication.
- Windowing.
- Output mode.
- Target table.
- Failure/retry behavior.

## 10.3 Lakeflow Connect

Lakeflow Connect provides managed ingestion connectors for databases, SaaS applications, cloud storage, and other sources.

It supports incremental ingestion patterns and is governed through Unity Catalog.

Migration relevance:

If the Databricks environment is being used as an ingestion platform rather than merely a storage platform, inventory the connector and pipeline separately from the destination table.

---

# 11. Lakeflow and Pipeline Dependencies

Databricks Lakeflow unifies:

- Lakeflow Connect for ingestion.
- Spark Declarative Pipelines for transformation.
- Lakeflow Jobs for orchestration.

Spark Declarative Pipelines support SQL and Python and can handle batch and streaming pipelines, data quality expectations, monitoring, retries, dependencies, and schema evolution.

Lakeflow Jobs orchestrate multi-step workflows and can run ETL, ML, and other tasks.

### Migration rule

Treat pipeline metadata as a separate migration layer:

`Source table → transformation → target table`

The table catalog describes the data object.

The migration plan must additionally describe how the transformation and orchestration logic will be recreated in Fabric.

---

# 12. Governance and Security

Unity Catalog provides centralized governance across Databricks data and AI assets.

Relevant capabilities include:

- Catalog-level permissions.
- Schema-level permissions.
- Table/view permissions.
- Column-level controls.
- Row-level security.
- Column masking.
- Data lineage.
- Audit information.
- Tags and descriptions.
- Data discovery.

### Migration rule

Governance should not be represented as a simple table-property conversion.

Extract:

- Object owner.
- Grants.
- Groups/principals.
- Row filters.
- Column masks.
- Tags.
- Sensitive columns.
- Lineage.
- External locations.
- Storage credentials.

Then map the **policy intent** to Microsoft Fabric governance.

Do not assume Databricks principal names or permissions exist unchanged in Fabric.

---

# 13. Databricks-to-Fabric Object Mapping

Primary target assumption: **Microsoft Fabric Lakehouse using Delta tables and Spark**.

| Databricks source | Fabric target | Mapping confidence | Migration guidance |
|---|---|---:|---|
| Catalog | Workspace/Lakehouse boundary | Low–Medium | Redesign based on governance and isolation |
| Schema | Lakehouse schema | High | Strong conceptual mapping |
| Delta table | Lakehouse Delta table | High | Validate Delta features/protocol |
| Managed table | Lakehouse managed Delta table | High | Recreate target storage/metadata |
| External table | Local Delta or Shortcut | Medium | Choose based on copy vs reference requirement |
| View | Fabric Spark/SQL view | Medium | Rewrite and validate SQL |
| Volume | Files / Shortcut / external storage | Medium | Depends on file usage |
| Partitioning | Fabric Delta layout | Medium | Redesign based on workload |
| Z-Order | Fabric optimization strategy | Low–Medium | Do not copy literally |
| Liquid clustering | Fabric optimization strategy | Low–Medium | Reassess physical design |
| MERGE | Fabric Spark/SQL MERGE where supported | Medium–High | Validate syntax and semantics |
| OPTIMIZE | Fabric Delta optimization | Medium | Translate intent, not command |
| VACUUM | Fabric Delta maintenance | Medium | Translate retention/maintenance intent |
| Unity Catalog | Fabric governance model | Low | Policy redesign required |
| External location | OneLake Shortcut / storage connection | Medium | Depends on source and security |
| Auto Loader | Fabric ingestion/streaming pattern | Low–Medium | Rebuild ingestion mechanism |
| Structured Streaming | Fabric Spark streaming/Eventstream pattern | Medium | Architecture redesign |
| Lakeflow Job | Fabric Data Factory/Fabric pipeline or other orchestration | Medium | Rebuild workflow |
| Spark notebook | Fabric notebook | Medium–High | Validate libraries, paths, APIs |
| Databricks SQL query | Fabric Spark SQL / Warehouse SQL | Medium | Dialect-specific conversion |
| SQL Warehouse | Fabric Warehouse / SQL endpoint | Medium | Depends on workload |
| Delta history | Target Delta history | Medium | Historical versions may require separate strategy |

**Important:** Mapping confidence is a migration-planning heuristic, not a guarantee of compatibility.

---

# 14. Microsoft Fabric Lakehouse Target Model

Microsoft Fabric Lakehouse provides:

- OneLake storage.
- Delta Lake tables.
- Apache Spark processing.
- SQL analytics access.
- Lakehouse schemas.
- OneLake shortcuts.

A schema-enabled Fabric Lakehouse uses:

`workspace.lakehouse.schema.table`

A default `dbo` schema exists in schema-enabled lakehouses.

Fabric can also reference external Delta data through OneLake shortcuts.

## 14.1 Fabric schema mapping

Databricks:

`catalog.schema.table`

Fabric:

`workspace.lakehouse.schema.table`

The schema level is the closest direct organizational mapping.

The catalog level requires architectural judgment.

## 14.2 Fabric Delta

Fabric Lakehouse uses Delta Lake as the default table format for reliable table storage and processing.

This makes Delta-to-Delta migrations particularly attractive.

However:

`Databricks Delta feature set != automatically identical Fabric behavior`

Validate:

- Delta protocol.
- Table features.
- Generated columns.
- Identity columns.
- Deletion vectors.
- Variant.
- Constraints.
- CDF.
- Time travel.
- Table properties.
- Optimization features.

## 14.3 OneLake Shortcuts

Fabric shortcuts can reference data without copying it.

Relevant sources include:

- Other Fabric lakehouses.
- Warehouses.
- ADLS Gen2.
- Amazon S3.
- Other supported sources.
- Delta tables.

Use a shortcut when:

- Data should not be duplicated.
- Source remains authoritative.
- Near-real-time access is required.
- Copying data is undesirable.

Prefer copying into local Fabric Delta tables when:

- Transformations are required.
- Target needs full table-management control.
- Compliance requires data to reside in a particular region.
- Independent schema/table lifecycle is required.

---

# 15. Databricks-to-Fabric Data Type Guidance

Primary mapping for a Fabric Lakehouse/Spark target:

| Databricks | Fabric Lakehouse/Spark | Action |
|---|---|---|
| BOOLEAN | BOOLEAN | Direct validation |
| TINYINT | TINYINT | Direct validation |
| SMALLINT | SMALLINT | Direct validation |
| INT | INT | Direct |
| BIGINT | BIGINT | Direct |
| FLOAT | FLOAT | Direct validation |
| DOUBLE | DOUBLE | Direct |
| DECIMAL(p,s) | DECIMAL(p,s) | Preserve p/s |
| STRING | STRING | Direct |
| VARCHAR(n) | VARCHAR(n)/STRING | Validate target usage |
| CHAR(n) | CHAR(n)/STRING | Validate semantics |
| BINARY | BINARY | Validate |
| DATE | DATE | Direct |
| TIMESTAMP | TIMESTAMP | Validate time-zone semantics |
| TIMESTAMP_NTZ | TIMESTAMP_NTZ or target equivalent | Validate |
| ARRAY<T> | ARRAY<T> | Validate nested support |
| MAP<K,V> | MAP<K,V> | Validate |
| STRUCT | STRUCT | Validate nested schema |
| VARIANT | VARIANT | Validate Fabric Spark runtime/Delta version |
| INTERVAL | Target-specific equivalent | Rewrite if required |

If the target is **Fabric Warehouse rather than Lakehouse**, perform a separate T-SQL/storage type mapping. Do not reuse the Spark/Lakehouse mapping blindly.

---

# 16. SQL Migration Risk Categories

Use these categories when assessing SQL:

## LOW

Likely straightforward:

- SELECT.
- INSERT.
- Simple UPDATE.
- Simple DELETE.
- Basic joins.
- Basic aggregations.
- Common CASE expressions.
- Standard window functions.

Still validate execution results.

## MEDIUM

Requires dialect testing:

- Complex date/time logic.
- JSON functions.
- Array/map/struct operations.
- Temporary objects.
- Advanced window functions.
- Complex MERGE.
- Dynamic SQL.
- Cross-catalog references.
- Vendor-specific functions.

## HIGH

Usually requires redesign or substantial rewrite:

- Databricks-specific SQL commands.
- Delta maintenance commands.
- Lakeflow pipeline SQL.
- Streaming-table syntax.
- Databricks-specific functions.
- Unity Catalog security syntax.
- External-location-dependent SQL.
- SQL depending on proprietary table features.

## VERY HIGH

Treat as architecture migration:

- Streaming pipelines.
- Auto Loader.
- Complex CDC.
- Lakeflow Jobs.
- Lakeflow Connect.
- Databricks-specific orchestration.
- ML/feature/model dependencies.
- Databricks Apps/Lakebase dependencies.

---

# 17. Databricks Features That Must Not Be Blindly Copied

The following should be treated as **intent to translate**, not literal migration artifacts:

- Unity Catalog permissions.
- Catalog hierarchy.
- External locations.
- Cluster configurations.
- Photon configuration.
- OPTIMIZE.
- ZORDER.
- Liquid clustering.
- VACUUM schedules.
- Auto Loader.
- Lakeflow Jobs.
- Lakeflow Connect.
- Spark Declarative Pipelines.
- Databricks SQL Warehouse configuration.
- Databricks-specific SQL functions.
- Databricks runtime configuration.
- Databricks secrets.
- Cloud-provider-specific storage paths.
- Databricks workspace-specific paths.

---

# 18. Important Source Limitations and Migration Risks

## 18.1 External Delta clients

Databricks documents limitations for external Delta clients. Some external clients cannot alter Databricks-specific table properties or perform maintenance operations such as `OPTIMIZE`, `VACUUM`, and `ANALYZE` on managed Delta tables.

### Migration implication

Do not assume that an external engine can fully manage a Databricks Delta table simply because it can read the underlying data.

## 18.2 Identity columns

Identity columns disable concurrent transactions on applicable Databricks Delta tables.

### Migration implication

Detect identity columns and assess whether the target needs:

- Generated keys.
- Preserved source keys.
- Re-keying.
- Application-generated identifiers.

## 18.3 Generated columns

Generated columns can upgrade the Delta writer protocol.

### Migration implication

Detect them explicitly because external/client compatibility may be affected.

## 18.4 Iceberg

Databricks also supports Apache Iceberg workloads.

Iceberg has its own compatibility and feature limitations, including restrictions around certain data types and partitioning behaviors.

### Migration rule

Never assume a Databricks table is Delta merely because it is a Databricks table.

Extract the actual format.

## 18.5 Fabric SQL vs Fabric Spark

Microsoft Fabric has multiple data-engineering/query surfaces.

A migration target must specify whether a table is primarily intended for:

- Fabric Lakehouse/Spark.
- Fabric SQL analytics endpoint.
- Fabric Warehouse.

The same Databricks SQL statement may have different migration effort depending on the selected Fabric target.

---

# 19. Migration Assessment Algorithm

For each Databricks table, use the following decision sequence.

## Step 1 — Identify object

Determine:

- Catalog.
- Schema.
- Table.
- Object type.
- Managed/external.
- Format.

## Step 2 — Inspect schema

Extract:

- Columns.
- Types.
- Nullability.
- Nested structures.
- Defaults.
- Generated columns.
- Identity.
- Comments.

## Step 3 — Inspect physical design

Extract:

- Location.
- Partitions.
- Clustering.
- Table properties.
- Delta features.
- File statistics if available.

## Step 4 — Inspect behavior

Determine whether the table is:

- Static.
- Incrementally loaded.
- CDC-driven.
- Streaming.
- MERGE-based.
- Frequently updated.
- Append-only.

## Step 5 — Inspect dependencies

Find:

- Views.
- Jobs.
- Pipelines.
- Notebooks.
- SQL queries.
- Dashboards.
- ML workloads.
- External consumers.

## Step 6 — Determine Fabric target

Select:

- Lakehouse table.
- Shortcut.
- Warehouse table.
- View.
- Other Fabric object.

## Step 7 — Evaluate compatibility

Assign:

- Direct.
- Minor rewrite.
- Major rewrite.
- Redesign.
- Unsupported/unknown.

## Step 8 — Generate migration guidance

For each table produce:

- Target object.
- Target name.
- Type mappings.
- SQL transformation.
- Data movement method.
- Physical-design recommendation.
- Governance action.
- Dependency action.
- Validation requirements.
- Risk.

---

# 20. Table-Level Migration Risk Rules

## Low risk

Typical conditions:

- Delta table.
- Simple primitive columns.
- No special Delta features.
- No complex SQL dependencies.
- No streaming.
- No external location dependency.
- Simple append/load pattern.

Recommended action:

`Direct Delta table migration with schema validation`

## Medium risk

Typical conditions:

- Complex data types.
- Partitioning.
- Views.
- MERGE.
- External tables.
- Advanced SQL.
- Schema evolution.
- CDF.
- Non-trivial governance.

Recommended action:

`Automated migration + targeted rewrite + validation`

## High risk

Typical conditions:

- Streaming.
- Auto Loader.
- Complex CDC.
- Generated/identity columns.
- Deletion vectors.
- Databricks-specific functions.
- Complex Lakeflow pipelines.
- Extensive Unity Catalog policies.
- Heavy external-storage dependencies.

Recommended action:

`Architecture redesign + controlled migration`

## Critical/unknown

Typical conditions:

- Unsupported/unknown table format.
- Unknown dependencies.
- Proprietary Databricks features.
- Missing source metadata.
- Unavailable underlying data.
- Target capability not validated.

Recommended action:

`Manual assessment required`

---

# 21. Source Table Catalog Output Guidance

The LLM generating the source table catalog should prefer this structure:

## Table

`catalog.schema.table`

### Description

Business/technical description.

### Object metadata

- Object type:
- Table type:
- Format:
- Managed/external:
- Storage location:
- Owner:

### Columns

For every column:

`name | source type | nullable | description | generated/identity | target type | migration note`

### Physical design

- Partition columns:
- Clustering:
- Delta features:
- File/layout considerations:

### Workload behavior

- Batch/streaming:
- Insert/update/delete pattern:
- MERGE:
- CDC:
- Schema evolution:

### Dependencies

- Views:
- Pipelines:
- Jobs:
- Notebooks:
- Consumers:

### Migration summary

- Target object:
- Migration strategy:
- Risk:
- Manual work:
- Validation:

---

# 22. Migration Plan Output Guidance

The LLM generating the migration plan should produce table-level guidance with:

### Source

`catalog.schema.table`

### Target

`workspace.lakehouse.schema.table`

### Strategy

One of:

- Direct copy.
- Delta-to-Delta migration.
- Copy and transform.
- Shortcut/reference.
- SQL rewrite.
- Streaming redesign.
- Manual migration.

### Schema transformation

List only actual transformations.

### SQL transformation

List source SQL/features requiring rewrite.

### Physical design

Explain whether source partitions/clustering should be:

- Preserved.
- Replaced.
- Removed.
- Re-designed.

### Data movement

Specify:

- Copy.
- Shortcut.
- Incremental load.
- CDC.
- Streaming.

### Governance

Specify:

- Owner mapping.
- Permissions.
- Sensitive columns.
- Row/column security.
- Lineage.

### Validation

Minimum validation should include:

- Row counts.
- Column counts.
- Data types.
- Null counts.
- Primary/business key uniqueness where applicable.
- Aggregates.
- Min/max values.
- Sample record comparison.
- Incremental behavior.
- Query result comparison.
- Performance comparison where required.

### Risk

Use:

`Low | Medium | High | Critical/Unknown`

and explain the reason.

---

# 23. Migration Validation

Migration should validate both **data correctness** and **behavioral equivalence**.

## Schema validation

Compare:

- Table names.
- Column names.
- Column order where relevant.
- Data types.
- Precision/scale.
- Nullability.
- Nested structures.
- Defaults.
- Generated properties.

## Data validation

Compare:

- Row counts.
- Distinct keys.
- Null counts.
- Min/max.
- Sums.
- Aggregates.
- Hash/checksum samples.
- Representative records.

## Functional validation

Compare:

- Query outputs.
- View results.
- MERGE behavior.
- CDC behavior.
- Streaming latency.
- Incremental processing.

## Operational validation

Check:

- Pipeline success.
- Retry behavior.
- Monitoring.
- Logging.
- Security.
- Permissions.
- Data lineage.
- SLA/performance.

A phased or side-by-side validation strategy reduces migration risk.

---

# 24. Performance Migration Rules

Do not directly reproduce Databricks performance tuning.

Instead identify the **reason** for each optimization.

| Source feature | Determine why it exists | Target action |
|---|---|---|
| Partitioning | Partition pruning / data isolation | Reassess target partition/layout |
| ZORDER | Data skipping | Use target-specific optimization |
| Liquid clustering | Adaptive data layout | Reassess target clustering/layout |
| OPTIMIZE | File compaction/layout | Use Fabric Delta maintenance |
| Photon | Faster execution | Do not migrate; benchmark target engine |
| Disk caching | Repeated-read performance | Benchmark target caching behavior |
| Predictive optimization | Automated maintenance | Use target-native optimization |
| Autoscaling | Compute elasticity | Redesign target compute |
| Job clusters | Cost/isolated execution | Redesign target pipeline compute |

---

# 25. Naming and Namespace Migration

Databricks source:

`catalog.schema.table`

Fabric Lakehouse target:

`workspace.lakehouse.schema.table`

Potential issues:

- Special characters.
- Reserved words.
- Case sensitivity.
- Name length.
- Duplicate names.
- Cross-workspace references.
- SQL quoting.
- Schema names.

Fabric schema names have naming constraints. Validate every source identifier before generating target DDL.

Never silently rename an object.

If renaming is required, record:

`source_name → target_name → reason`

and update dependent SQL accordingly.

---

# 26. Dependency-Aware Migration

Table migration order should respect dependencies.

Recommended logical order:

1. Storage/access foundations.
2. Schemas.
3. Base/raw tables.
4. Reference/master tables.
5. Transformation tables.
6. Views.
7. Aggregates/materialized objects.
8. Pipelines.
9. Dashboards/consumers.
10. Streaming/CDC activation.

For a DAG:

`A → B → C`

migrate/validate A before B, and B before C.

Do not generate a table-level plan without considering downstream dependencies when those dependencies are available.

---

# 27. Retrieval Keywords and Synonyms

Use these terms as equivalent retrieval concepts where appropriate:

### Namespace

- catalog
- database
- schema
- three-level namespace
- catalog.schema.table
- object hierarchy

### Table

- relation
- Delta table
- managed table
- external table
- streaming table
- physical table

### Storage

- object storage
- cloud storage
- external location
- managed location
- Delta files
- transaction log

### Governance

- Unity Catalog
- UC
- catalog governance
- access control
- permissions
- row-level security
- column masking
- lineage

### Processing

- Spark
- Spark SQL
- Databricks SQL
- Photon
- Lakeflow
- Spark Declarative Pipelines

### Migration

- Databricks migration
- Delta migration
- Fabric migration
- table conversion
- schema conversion
- SQL rewrite
- workload migration
- platform migration

---

# 28. High-Value Retrieval Rules for the Migration LLM

When answering migration questions:

1. Prefer table-level metadata over generic platform descriptions.
2. Determine the actual table format before recommending migration.
3. Distinguish managed and external tables.
4. Detect Delta-specific features before recommending direct migration.
5. Treat Databricks-specific optimizations as redesign candidates.
6. Treat Unity Catalog as governance intent, not a direct object-copy operation.
7. Preserve precision and scale for DECIMAL.
8. Inspect nested types recursively.
9. Inspect streaming and CDC dependencies.
10. Inspect SQL dependencies before migrating views/tables.
11. Do not assume Databricks SQL is identical to Fabric SQL.
12. Distinguish Fabric Lakehouse/Spark from Fabric Warehouse.
13. Prefer Delta-to-Delta migration when the source and target table semantics are compatible.
14. Use OneLake Shortcuts when avoiding data duplication is an explicit requirement and the source is suitable.
15. Recommend copy/transform when the target needs independent ownership or transformation.
16. Flag unsupported or unverified features instead of inventing a mapping.
17. Preserve business semantics even when physical implementation changes.
18. Separate data migration from workload migration.
19. Generate migration guidance at the table/object level whenever possible.
20. Explicitly state assumptions when source metadata is incomplete.

---

# 29. Compact Feature Decision Matrix

| Databricks feature | Migration treatment |
|---|---|
| Delta table | Prefer Delta-to-Delta |
| Simple primitive schema | Usually low risk |
| Nested ARRAY/MAP/STRUCT | Validate recursively |
| VARIANT | Validate target runtime/protocol |
| DECIMAL | Preserve precision/scale |
| Managed table | Recreate as target-managed Delta |
| External table | Evaluate copy vs Shortcut |
| Catalog | Redesign boundary |
| Schema | Map to Fabric Lakehouse schema where appropriate |
| View | Rewrite/validate SQL |
| MERGE | Preserve semantics; validate target support |
| Generated column | Explicit conversion |
| Identity column | Explicit key strategy |
| CDF | Validate incremental target strategy |
| Time travel | Validate history/retention requirements |
| Deletion vectors | Validate Delta feature compatibility |
| Partitioning | Reassess |
| ZORDER | Reassess |
| Liquid clustering | Reassess |
| OPTIMIZE | Translate optimization intent |
| VACUUM | Translate retention/maintenance intent |
| Auto Loader | Rebuild ingestion |
| Structured Streaming | Rebuild streaming architecture |
| Lakeflow Jobs | Rebuild orchestration |
| Unity Catalog permissions | Rebuild governance policies |
| Databricks secrets | Rebuild credential management |
| Photon | Benchmark target; do not migrate configuration |
| Databricks runtime | Replace with target runtime |

---

# 30. Minimum Source Metadata Required Before Migration Recommendation

A reliable migration recommendation should ideally have:

- Fully qualified object name.
- Object type.
- Table format.
- Managed/external status.
- Columns and types.
- Table location.
- Partition information.
- Table properties.
- Delta features.
- Generated/identity columns.
- Constraints.
- View definitions where applicable.
- Upstream/downstream dependencies.
- Batch/streaming classification.
- CDC/merge behavior.
- SQL usage.
- Governance requirements.

If several of these are unavailable, the LLM should lower confidence and label the migration plan as requiring further assessment.

---

# 31. Canonical Migration Decision Template

For any Databricks object, use:

```text
SOURCE:
<catalog>.<schema>.<object>

OBJECT TYPE:
<table/view/streaming table/etc.>

SOURCE FORMAT:
<Delta/Iceberg/other>

SOURCE STORAGE:
<managed/external/location>

TARGET:
<Fabric workspace/lakehouse/schema/object>

TARGET FORMAT:
<Delta/etc.>

MIGRATION STRATEGY:
<direct copy / transform / shortcut / rewrite / redesign>

SCHEMA MAPPING:
<summary>

SPECIAL FEATURES:
<Delta features, streaming, CDC, generated columns, etc.>

SQL CHANGES:
<required rewrites>

PHYSICAL DESIGN:
<partition/clustering recommendation>

GOVERNANCE:
<security/permissions mapping>

DEPENDENCIES:
<upstream/downstream>

VALIDATION:
<tests>

RISK:
<Low/Medium/High/Critical>

CONFIDENCE:
<High/Medium/Low>

MANUAL ACTIONS:
<required human decisions>
```

---

# 32. Final Migration Principles

- **Delta compatibility is helpful but not sufficient.**
- **A Databricks table is more than its columns.**
- **Catalog/schema structure carries governance meaning.**
- **Managed vs external storage materially affects migration.**
- **Physical optimizations should be redesigned, not copied blindly.**
- **Streaming and CDC workloads require architecture-level migration.**
- **Databricks SQL requires dialect validation.**
- **Complex data types require recursive schema analysis.**
- **Unity Catalog policies require target governance redesign.**
- **Table history and Delta features may require explicit preservation strategies.**
- **Fabric Lakehouse is the closest conceptual target for Delta/Spark-centric Databricks workloads.**
- **Fabric Warehouse should be treated as a separate SQL/storage target.**
- **When information is missing, the migration system should report uncertainty rather than fabricate compatibility.**
- **The migration plan should preserve business behavior, not merely reproduce source syntax.**

## Source References

Databricks:
- Architecture: https://docs.databricks.com/aws/en/getting-started/architecture
- Reference architectures: https://docs.databricks.com/aws/en/lakehouse-architecture/reference
- Catalogs: https://docs.databricks.com/aws/en/catalogs/
- Schemas: https://docs.databricks.com/aws/en/schemas
- Unity Catalog securable objects: https://docs.databricks.com/aws/en/data-governance/unity-catalog/securable-objects
- SQL language reference: https://docs.databricks.com/aws/en/sql/language-manual/
- Delta Lake: https://docs.databricks.com/aws/en/delta
- Data types: https://docs.databricks.com/aws/en/sql/language-manual/
- Tables: https://docs.databricks.com/aws/en/tables
- Lakeflow: https://docs.databricks.com/aws/en/ldp/
- Optimization: https://docs.databricks.com/aws/en/optimizations

Microsoft Fabric:
- Lakehouse overview: https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-overview
- Lakehouse and Delta tables: https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-and-delta-tables
- Lakehouse schemas: https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-schemas
- OneLake shortcuts: https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-shortcuts
- Fabric Data Engineering: https://learn.microsoft.com/en-us/fabric/data-engineering/

Source documents supplied for this knowledge base:
- Databricks Big Book of Data Engineering.
- The Data Intelligence Platform For Dummies, 2nd Databricks Special Edition.
