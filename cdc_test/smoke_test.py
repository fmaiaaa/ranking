"""Read-only Salesforce CDC smoke test via Streaming API (CometD)."""
import argparse
import json
import os
import sys
import time
from urllib.parse import urlparse

import requests
from simple_salesforce import Salesforce


def required(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing GitHub Secret: {name}")
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=90)
    args = parser.parse_args()
    if not 10 <= args.seconds <= 240:
        raise ValueError("--seconds must be 10..240")
    sf = Salesforce(
        username=required("SF_USERNAME"),
        password=required("SF_PASSWORD"),
        security_token=required("SF_SECURITY_TOKEN"),
        domain=os.getenv("SF_DOMAIN", "login"),
    )
    base = f"https://{sf.sf_instance}"
    headers = {"Authorization": f"Bearer {sf.session_id}", "Content-Type": "application/json"}
    session = requests.Session()
    print("AUTH_OK (credentials and records are not printed)", flush=True)

    # Discovery: no data records downloaded.
    for obj in ("Opportunity", "Event", "Avaliacao_credito__c"):
        response = session.get(f"{base}/services/data/v60.0/sobjects/{obj}/describe",
                               headers=headers, timeout=30)
        print(f"OBJECT {obj}: HTTP {response.status_code}", flush=True)
    endpoint = f"{base}/cometd/60.0"
    def send(messages, timeout=45):
        r = session.post(endpoint, headers=headers, json=messages, timeout=timeout)
        r.raise_for_status()
        result = r.json()
        if not isinstance(result, list):
            raise RuntimeError("Unexpected CometD response")
        return result

    handshake = send([{
        "channel": "/meta/handshake",
        "version": "1.0",
        "minimumVersion": "1.0",
        "supportedConnectionTypes": ["long-polling"],
        "ext": {"replay": True},
    }])
    hello = next((m for m in handshake if m.get("channel") == "/meta/handshake"), {})
    if not hello.get("successful"):
        print("HANDSHAKE_FAILED", str(hello.get("error", "unknown"))[:180])
        return 2
    client = hello["clientId"]
    print("CDC_HANDSHAKE_OK", flush=True)
    topics = [
        "/data/OpportunityChangeEvent",
        "/data/EventChangeEvent",
        "/data/Avaliacao_credito__ChangeEvent",
    ]
    subscribed = []
    for idx, topic in enumerate(topics, 1):
        result = send([{
            "channel": "/meta/subscribe", "clientId": client,
            "subscription": topic, "id": str(idx),
            "ext": {"replay": {topic: -1}},
        }])
        ack = next((m for m in result if m.get("channel") == "/meta/subscribe"), {})
        ok = bool(ack.get("successful"))
        print(f"SUBSCRIBE {topic}: {'OK' if ok else 'FAILED'} {str(ack.get('error', ''))[:140]}", flush=True)
        if ok:
            subscribed.append(topic)
    if not subscribed:
        print("No CDC channels available. Check CDC enablement and permissions.")
        return 3

    deadline = time.monotonic() + args.seconds
    received = 0
    try:
        while time.monotonic() < deadline:
            result = send([{
                "channel": "/meta/connect", "clientId": client,
                "connectionType": "long-polling",
            }], timeout=45)
            for msg in result:
                channel = msg.get("channel", "")
                if channel in subscribed:
                    header = msg.get("data", {}).get("payload", {}).get("ChangeEventHeader", {})
                    # No customer names, record IDs, field values or payloads in logs.
                    print("CDC_EVENT", json.dumps({
                        "channel": channel,
                        "changeType": header.get("changeType"),
                        "changedFieldCount": len(header.get("changedFields") or []),
                    }), flush=True)
                    received += 1
                elif channel == "/meta/connect" and not msg.get("successful", False):
                    print("CONNECT_FAILED", str(msg.get("error", "unknown"))[:140])
                    return 4
    finally:
        try:
            send([{"channel": "/meta/disconnect", "clientId": client}], timeout=10)
        except requests.RequestException:
            pass
    print(f"TEST_COMPLETE subscribed={len(subscribed)} events_received={received}", flush=True)
    print("Zero events is not a failure: no change may have occurred during the window.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"TEST_ERROR {type(exc).__name__}: check GitHub Actions logs and permissions", flush=True)
        sys.exit(1)
