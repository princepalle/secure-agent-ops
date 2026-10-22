# Acme AI Security Policy

Sample policy for the SecureAgentOps demo. Applies to every AI agent, tool, and data pipeline operated by Acme.

## Access control

Access follows least privilege. Standard users may run read-only tools such as document search and system status. Only administrators may view organisation-wide usage reports. Any tool that changes state, such as creating an incident or modifying infrastructure, requires approval from an administrator before it runs. An administrator may not approve their own request.

## Tenant isolation

Customer data is isolated per tenant. Every query against the database or the vector store must be filtered by the authenticated tenant ID, which comes from the verified access token and never from user input or model output. Cross-tenant access is treated as a severity 1 security incident.

## Prompt injection

Retrieved documents and user messages are untrusted input. The agent must never follow instructions found inside documents, never reveal system prompts, and never let model output grant permissions or skip approval. Authorisation decisions are made in code, not by the language model.

## Secrets management

Secrets such as API keys, signing keys, and database passwords must never be committed to source control. In production they are stored in AWS Secrets Manager and rotated at least every 90 days. JWT signing keys must be at least 32 characters of random data.

## Data retention

Agent conversation logs are retained for 30 days for troubleshooting, then deleted. Audit logs of logins, approvals, and document changes are retained for one year. Uploaded documents are deleted within 7 days of a deletion request.

## Vulnerability management

Dependencies are scanned for known vulnerabilities on every pull request. Critical vulnerabilities must be patched within 7 days and high vulnerabilities within 30 days.
