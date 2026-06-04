# Change History

---

## Remove CentOps Integration

**Date:** 2026-05-18
**Branch:** `152/remove_centOps_connection`

| Commit | Description |
|--------|-------------|
| `c43961b` (`c43961b8925b25f66bbd598dc6412379b5d3a6fd`) | Core removal — frontend, backend DSL, infrastructure, database migration |
| `57332b6` (`57332b6f44d22fb7caeedaa1bdc3f24dadd3b2ea`) | Remove CentOps env vars from `GUI/entrypoint.sh` |
| `a7675cf` (`a7675cfa8b384c2b99b6e9f95e08480d98026589`) | Fix remaining `external_id` agency SQL queries and clean up test fixtures |

### Background
CKB previously integrated with CentOps to identify agencies by their CentOps client ID (`externalId`). Since data is now pulled into CKB directly from each agency's own knowledge base, distinguishing institutions with a CentOps identifier is no longer needed.

### Changes

#### Frontend (GUI)
| File | Change |
|------|--------|
| `GUI/src/services/centops.ts` | **Deleted** — entire CentOps API service (interfaces, pagination, options fetcher) |
| `GUI/src/pages/Agency/SaveAgency.tsx` | Removed CentOps import, `useQuery`, `externalId` form field, loading/error UI |
| `GUI/src/services/agencies.ts` | Removed `externalId` from all interfaces and API request bodies |
| `GUI/translations/en/common.json` | Removed keys: `centops`, `centopsLoadError`, `loadingCentopsOptions` |
| `GUI/translations/et/common.json` | Removed keys: `centops`, `centopsLoadError`, `loadingCentopsOptions` |
| `GUI/.env.example` | Removed `REACT_APP_CENTOPS_API_URL/KEY/SECRET` |
| `GUI/.env.development` | Removed `REACT_APP_CENTOPS_API_URL/KEY/SECRET` |
| `GUI/entrypoint.sh` | Removed CentOps vars from `envsubst` |

#### Backend (DSL)
| File | Change |
|------|--------|
| `DSL/Ruuter/ckb/POST/agency/add.yml` | Removed `externalId` from allowlist, assignment, and request body |
| `DSL/Ruuter/ckb/POST/agency/edit.yml` | Removed `externalId` from allowlist, assignment, and request body |
| `DSL/Ruuter/ckb/GET/auth/tara/login.yml` | Updated stale `namespace: centops` → `namespace: ckb-tara` |
| `DSL/Resql/ckb/POST/agency/create_agency.sql` | Removed `external_id` from allowlist, response fields, `INSERT`, and `RETURNING` |
| `DSL/Resql/ckb/POST/agency/update_agency.sql` | Removed `external_id` from allowlist and update array |
| `DSL/Resql/ckb/GET/agency/get_agency.sql` | Removed `external_id` from response fields and `SELECT` |
| `DSL/Resql/ckb/GET/agency/list_agency_data_available.sql` | Replaced `external_id` with `base_id` in `SELECT` and `WHERE` filter |
| `DSL/Resql/ckb/GET/agency/list_agency_data_hash.sql` | Replaced `external_id` with `base_id` in `SELECT` and `WHERE` filter |

#### Infrastructure
| File | Change |
|------|--------|
| `docker-compose.yml` | Removed `REACT_APP_CENTOPS_API_URL/KEY/SECRET` from `gui` service |
| `charts/ckb/values.yaml` | Removed `REACT_APP_CENTOPS_API_URL/KEY/SECRET` from Helm values |

#### Database
| File | Change |
|------|--------|
| `DSL/Liquibase/changelog/20260518120000-remove-agency-external-id.sql` | Drops `external_id` column from `agency_management.agency` |
| `DSL/Liquibase/changelog/20260518120000-remove-agency-external-id.xml` | Liquibase changeset definition |
| `DSL/Liquibase/changelog/20260518120000-rollback.sql` | Restores `external_id TEXT` column on rollback |
| `DSL/Liquibase/data/test-fixtures/agency.csv` | Removed `external_id` column, removed ARVA rows, kept TEST agency rows only |

---
