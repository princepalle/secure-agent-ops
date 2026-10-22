# Globex On-Call Handbook

Sample content for the SecureAgentOps demo. This document belongs to the Globex tenant only and must never appear in another tenant's search results.

## Rotation

Globex runs a follow-the-sun on-call rotation across three regions. Each shift lasts 8 hours. Hand-offs happen in the #globex-oncall channel with a written summary of open issues.

## Escalation codeword

When a Globex incident requires executive attention, the on-call engineer uses the escalation codeword BLUE-HERON in the incident channel. The codeword is confidential to Globex.

## Paging policy

Globex pages only for customer-facing impact. Internal tooling failures are handled the next business day. The on-call engineer must acknowledge a page within 10 minutes.

## Model provider outages

If the model provider is unavailable for more than 10 minutes, Globex switches the support assistant to a static FAQ mode and posts a banner on the help centre.
