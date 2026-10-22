# Acme Incident Response Runbook

Sample runbook for the SecureAgentOps demo.

## Severity levels

- SEV1: Complete outage, data exposure, or cross-tenant access. Page the on-call engineer and the security lead immediately. Status page update within 15 minutes.
- SEV2: Major degradation, such as agent p95 latency above 10 seconds or more than 5% of requests failing. Page the on-call engineer. Status update within 30 minutes.
- SEV3: Partial degradation with a workaround, such as one tool failing. Handle during business hours. Create a ticket within 4 hours.
- SEV4: Minor issue or cosmetic bug. Add to the backlog.

## First 15 minutes

Acknowledge the page within 5 minutes. Open an incident record with a clear title, severity, and affected component. Assign an incident commander. Check the system status dashboard for the API, database, vector store, and model provider. If the model provider is degraded, switch the agent to the fallback model.

## Escalation

If the incident is not mitigated within 30 minutes, escalate to the platform engineering manager. For SEV1 incidents involving customer data, notify the security lead and legal within 1 hour. Never share customer data in public channels while investigating.

## Vector store or retrieval failures

If retrieval latency exceeds 1 second or the vector store is unreachable, the agent should answer that it cannot search documents rather than answering without sources. Check disk space and index health, then restart the vector store service. Rebuild the index from the document metadata if corruption is detected.

## Post-incident review

Every SEV1 and SEV2 incident requires a blameless post-incident review within 5 business days. The review records the timeline, root cause, customer impact, and action items with owners and due dates.
