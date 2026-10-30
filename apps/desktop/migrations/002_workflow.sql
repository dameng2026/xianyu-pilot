CREATE TABLE wf_tenants (id TEXT PRIMARY KEY, name TEXT NOT NULL);
INSERT INTO wf_tenants VALUES ('local', '我的本机工作区');
CREATE TABLE wf_accounts (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES wf_tenants(id),
    name TEXT NOT NULL, identity TEXT NOT NULL, driver TEXT NOT NULL,
    endpoint TEXT NOT NULL, credential BLOB, status TEXT NOT NULL DEFAULT 'unverified',
    verified INTEGER NOT NULL DEFAULT 0, generation INTEGER NOT NULL DEFAULT 1,
    evidence TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL,
    UNIQUE(tenant_id,id)
);
CREATE TABLE wf_services (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES wf_tenants(id),
    account_id TEXT NOT NULL, name TEXT NOT NULL, settings TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1, enabled INTEGER NOT NULL DEFAULT 1,
    UNIQUE(tenant_id,id),
    FOREIGN KEY(tenant_id,account_id) REFERENCES wf_accounts(tenant_id,id)
);
CREATE TABLE wf_products (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES wf_tenants(id),
    name TEXT NOT NULL, external_id TEXT NOT NULL, settings TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1, enabled INTEGER NOT NULL DEFAULT 1,
    UNIQUE(tenant_id,id), UNIQUE(tenant_id,external_id)
);
CREATE TABLE wf_tasks (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES wf_tenants(id),
    order_key TEXT NOT NULL, product_id TEXT NOT NULL,
    state TEXT NOT NULL, document TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(tenant_id,id), UNIQUE(tenant_id,order_key),
    FOREIGN KEY(tenant_id,product_id) REFERENCES wf_products(tenant_id,id)
);
CREATE INDEX wf_tasks_queue ON wf_tasks(tenant_id,state,created_at);
CREATE TABLE wf_revisions (
    task_id TEXT NOT NULL REFERENCES wf_tasks(id), revision INTEGER NOT NULL,
    snapshot TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(task_id,revision)
);
CREATE TABLE wf_attempts (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
    task_id TEXT NOT NULL, stage TEXT NOT NULL, attempt INTEGER NOT NULL,
    status TEXT NOT NULL, receipt TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL,
    UNIQUE(task_id,stage,attempt),
    FOREIGN KEY(tenant_id,task_id) REFERENCES wf_tasks(tenant_id,id)
);
CREATE TABLE wf_inbox (
    tenant_id TEXT NOT NULL, event_id TEXT NOT NULL, task_id TEXT NOT NULL,
    payload TEXT NOT NULL, PRIMARY KEY(tenant_id,event_id),
    FOREIGN KEY(tenant_id,task_id) REFERENCES wf_tasks(tenant_id,id)
);
CREATE TABLE wf_channels (
    tenant_id TEXT NOT NULL, account_id TEXT NOT NULL, target TEXT NOT NULL,
    task_id TEXT, frozen_reason TEXT, PRIMARY KEY(tenant_id,account_id,target),
    FOREIGN KEY(tenant_id,account_id) REFERENCES wf_accounts(tenant_id,id)
);
CREATE TABLE wf_audit (
    id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES wf_tenants(id),
    entity_id TEXT NOT NULL, action TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX wf_audit_entity ON wf_audit(tenant_id,entity_id,id);
