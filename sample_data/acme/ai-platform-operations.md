# Acme AI Platform Operations Guide

This guide describes how the Acme platform team runs production AI workloads. It is sample content for the SecureAgentOps demo.

## Service level objectives

The agent API targets 99.9% monthly availability. The p95 latency objective for agent requests is 4 seconds end to end, including retrieval and model inference. Retrieval alone should stay under 300 milliseconds at p95. Error budget burn above 2% per hour pages the on-call engineer.

## Model deployment

New model versions are rolled out with a canary. The canary receives 5% of traffic for at least 24 hours. Promotion requires that the evaluation suite passes, hallucination rate does not regress by more than 1 percentage point, and cost per request stays within 10% of the previous version. Every model version is pinned by ID; floating aliases such as "latest" are not allowed in production.

## Capacity and cost controls

Each tenant has a monthly token budget. When a tenant reaches 80% of its budget, administrators receive an alert. At 100%, requests are throttled to the free tier rate. Inference cost is reviewed weekly using per-tenant usage reports. Prompt caching should be enabled for system prompts longer than 1,000 tokens.

## Vector store maintenance

The vector index is rebuilt when the embedding model changes, because vectors from different embedding models are not comparable. Documents are re-chunked with a chunk size of 800 characters and an overlap of 120 characters. Orphaned vectors are cleaned up nightly by comparing the index with the document metadata table.

## Observability

All services emit OpenTelemetry traces with the tenant hashed, never in plain text. Dashboards track request rate, latency percentiles, tool failures, retrieval latency, and token usage. Token counts reported by the model provider are labelled measured; heuristic counts are labelled estimated and must never be presented as billing data.
