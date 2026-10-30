CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    client_request_id TEXT NOT NULL UNIQUE,
    input TEXT NOT NULL,
    scenario TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN
        ('queued','sending','waiting_reply','completed','failed_safe','unknown','timed_out')),
    result TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX tasks_status_created ON tasks(status, created_at);
CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    payload TEXT NOT NULL
);
CREATE TABLE journal (
    id INTEGER PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX journal_task ON journal(task_id, id);
CREATE TABLE channel (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    frozen_reason TEXT
);
INSERT INTO channel(id) VALUES (1);
CREATE TABLE ui_sessions (
    digest TEXT PRIMARY KEY,
    expires REAL NOT NULL
);
