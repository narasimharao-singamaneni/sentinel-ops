# API Specification

## Overview

Person API provides CRUD operations for managing corporate workers. A single `workers` table in PostgreSQL stores all records; `status` column (`'active'` or `'inactive'`) separates them.

All worker endpoints are mounted under their respective prefixes:
- `/api/v1/active/workers`
- `/api/v1/inactive/workers`

## Data Model

### Worker

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Auto-generated on create |
| `name` | string (max 255) | Worker's display name |
| `status` | enum | `'active'` or `'inactive'` |
| `created_at` | timestamp | Auto-set on create |
| `updated_at` | timestamp | Auto-updated on modify |

## Endpoints

### Create Worker

```
POST /api/v1/{active|inactive}/workers
Content-Type: application/json
```

**Request Body:**
```json
{
  "name": "Jane Doe",
  "status": "active"
}
```

`status` must match the endpoint prefix. Submitting `"inactive"` to the active endpoint returns 400.

**Success:** `201 Created` — returns `WorkerOut`
**Errors:** `400 Bad Request`, `422 Validation Error`

### Get Worker

```
GET /api/v1/{active|inactive}/workers/{id}
```

**Success:** `200 OK` — returns `WorkerOut`
**Errors:** `404 Not Found`

**Caching:** Active workers are cached for 600 seconds. Inactive workers are not cached.

### Update Worker

```
PUT /api/v1/{active|inactive}/workers/{id}
Content-Type: application/json
```

**Request Body** (all fields optional):
```json
{
  "name": "Jane Updated",
  "status": "active"
}
```

Changing `status` to a value different from the endpoint prefix returns 400.

**Success:** `200 OK` — returns updated `WorkerOut`. Active worker cache is invalidated.
**Errors:** `400 Bad Request`, `404 Not Found`

### Delete Worker

```
DELETE /api/v1/{active|inactive}/workers/{id}
```

**Success:** `200 OK` — `{"message": "Worker deleted successfully"}`
**Errors:** `404 Not Found`

Active worker cache is invalidated on delete.

### List Workers

```
GET /api/v1/{active|inactive}/workers?limit=20&offset=0
```

**Query Parameters:**

| Param | Type | Default | Min | Max | Description |
|-------|------|---------|-----|-----|-------------|
| `limit` | int | 20 | 1 | 100 | Number of results per page |
| `offset` | int | 0 | 0 | — | Number of records to skip |

**Success:** `200 OK` — returns `WorkerListResponse`
```json
{
  "workers": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "name": "Jane Doe",
      "status": "active",
      "created_at": "2024-01-15T10:30:00Z",
      "updated_at": "2024-01-15T10:30:00Z"
    }
  ],
  "total": 49999
}
```

**Errors:** `400 Bad Request` (invalid limit/offset)

## System Endpoints

### Health Check

```
GET /health
```

Returns `200 OK` with `{"status": "healthy"}`. Not subject to concurrency limit.

### Documentation

```
GET /
```

Redirects (`307`) to `/docs` (Swagger UI).

GET `/openapi.json` returns the OpenAPI 3.1 spec.

## Error Response Format

```json
{
  "detail": "Human-readable error message"
}
```

| Status | Meaning |
|--------|---------|
| `400` | Bad request (e.g., status mismatch, limit exceeded) |
| `404` | Worker not found |
| `422` | Validation error (Pydantic) |
| `429` | Rate limited |
| `503` | Too many concurrent requests |
| `500` | Internal server error (never leaks DB internals) |

## Concurrency Model

- Maximum **10 concurrent** requests across the entire API.
- Requests beyond the limit receive `503 Service Unavailable`.
- Health checks, docs, and OpenAPI endpoints bypass the limit.
- Semaphore timeout: 3 seconds (requests exceeding this receive 503).

## Caching Strategy

| Endpoint | Cached? | TTL | Invalidation |
|----------|---------|-----|--------------|
| Active GET (by ID) | Yes | 600s | On PUT/DELETE for same ID |
| Active PUT | No (invalidates GET) | — | — |
| Active DELETE | No (invalidates GET) | — | — |
| Inactive GET (by ID) | No | — | — |
| Inactive PUT | No | — | — |
| Inactive DELETE | No | — | — |
| List endpoints | No | — | — |

## Pagination

All list endpoints return a `total` count alongside `workers`. With 150K records and `limit=100`, maximum pages = 1,500.

```
GET /api/v1/inactive/workers?limit=100&offset=0   → page 1
GET /api/v1/inactive/workers?limit=100&offset=100  → page 2
```
