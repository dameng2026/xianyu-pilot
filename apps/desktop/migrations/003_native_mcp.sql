CREATE TABLE wf_mcp_grants (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES wf_tenants(id),
    name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, scope TEXT NOT NULL,
    expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
    window_start REAL NOT NULL DEFAULT 0, window_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE wf_connector_state (
    tenant_id TEXT NOT NULL, account_id TEXT NOT NULL,
    last_seen REAL NOT NULL DEFAULT 0, dropped_through INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(tenant_id,account_id),
    FOREIGN KEY(tenant_id,account_id) REFERENCES wf_accounts(tenant_id,id)
);
CREATE TABLE wf_connector_events (
    id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL, account_id TEXT NOT NULL,
    generation INTEGER NOT NULL, message_key TEXT NOT NULL, sender_id TEXT NOT NULL,
    content TEXT NOT NULL, reply_to TEXT, received REAL NOT NULL,
    UNIQUE(tenant_id,account_id,message_key),
    FOREIGN KEY(tenant_id,account_id) REFERENCES wf_accounts(tenant_id,id)
);
