# Salesforce CDC smoke test

This is an isolated, **read-only** test. It does not modify the ranking application or Salesforce records.

## Setup
1. Create a GitHub Environment named `salesforce-cdc-test` (Settings → Environments).
2. Add environment secrets `SF_USERNAME`, `SF_PASSWORD`, `SF_SECURITY_TOKEN`, `SF_DOMAIN` (`login` for production or `test` for sandbox). Never commit a `.env` file.
3. Enable Change Data Capture for the desired objects in Salesforce Setup, and ensure the integration user has API access and permission to subscribe to change events.
4. Merge the PR after reviewing it. In Actions → Salesforce CDC smoke test → Run workflow, choose the duration. (Alternatively select the test branch if GitHub exposes it.)
5. Inspect job logs for AUTH_OK, object describe HTTP codes, CDC_HANDSHAKE_OK, SUBSCRIBE and TEST_COMPLETE.

## Interpretation
- `SUBSCRIBE ... OK`: channel subscription accepted, **not** proof that a business event occurred.
- `CDC_EVENT`: an actual change event was received during the listening window.
- `events_received=0`: inconclusive about live delivery; no relevant change may have occurred.
- Failure to subscribe: check object CDC support, org settings, permissions, and API access.

Uses Salesforce Streaming API (CometD long polling) to validate CDC, **not** Pub/Sub gRPC. It does not implement persistent replay storage, production synchronization, or continuous monitoring. GitHub Actions is ephemeral. Logs intentionally exclude customer records and record IDs. The subscription starts at replay -1 (new events only).
