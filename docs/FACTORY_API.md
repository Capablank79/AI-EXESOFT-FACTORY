# Factory API

Run the ASGI application with `uvicorn api.main:app`. PostgreSQL connection
settings use the existing `infrastructure.db` environment configuration.

## Authentication

Every route except `GET /health` requires `Authorization: Bearer <token>`. The
API never stores raw tokens. Configure `EXESOFT_API_TOKEN_USERS` as a JSON object
whose keys are lowercase SHA-256 token digests and whose values are PostgreSQL
user UUIDs. For example, the structure is:

```json
{"<sha256-token-digest>": "<user-uuid>"}
```

Provision high-entropy tokens through the operator's approved secret-management process and
keep the raw value with the client only. The API resolves the user on each
request and reads active status, role, and tenant from PostgreSQL. It does not
accept caller-supplied tenant or role fields.

Roles:

- `viewer`: read projects, tasks, and runs.
- `qa`: read and execute/resume project runs.
- `developer`: read, create projects/tasks, plan projects, and execute/resume.
- `admin`: all listed operations.

Cross-tenant project and task lookups return `404`. Task state cannot be changed
directly through the API; execution uses the PostgreSQL Runtime, Sandy adapters,
and the persisted Quality Gate.

## Routes

- `GET /health`
- `GET /projects?limit=100`
- `POST /projects` with `{ "name": "...", "request": "..." }`
- `GET /projects/{project_id}`
- `POST /projects/{project_id}/plan`
- `GET /projects/{project_id}/tasks`
- `POST /projects/{project_id}/tasks` with task title, role, and optional description,
  dependencies, retry limit, and priority
- `GET /projects/{project_id}/tasks/{task_id}`
- `GET /projects/{project_id}/runs`
- `POST /projects/{project_id}/run` or `/resume`, optionally with
  `{ "max_steps": 20 }`

Validation errors use a stable JSON envelope: `{ "error": { "code": "...",
"message": "..." } }`. Database errors return a generic `503` response without
SQL or connection details.
