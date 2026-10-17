import os
import time
import requests
from simple_salesforce import Salesforce

def main():
    sf = Salesforce(username=os.environ["SALESFORCE_USER"], password=os.environ["SALESFORCE_PASSWORD"], security_token=os.environ["SALESFORCE_TOKEN"], domain=os.getenv("SALESFORCE_DOMAIN") or "login")
    session = requests.Session()
    session.headers.update({"Authorization": "Bearer " + sf.session_id, "Content-Type": "application/json"})
    endpoint = "https://" + sf.sf_instance + "/cometd/60.0"
    def send(messages, timeout=40):
        response = session.post(endpoint, json=messages, timeout=timeout)
        response.raise_for_status()
        return response.json()
    response = send([{"channel":"/meta/handshake","version":"1.0","minimumVersion":"1.0","supportedConnectionTypes":["long-polling"]}])
    handshake = next((item for item in response if item.get("channel") == "/meta/handshake"), {})
    if not handshake.get("successful"):
        print("HANDSHAKE_FAILED")
        return 1
    client_id = handshake["clientId"]
    response = send([{"channel":"/meta/subscribe","clientId":client_id,"subscription":"/data/ChangeEvents","id":"1"}])
    subscription = next((item for item in response if item.get("channel") == "/meta/subscribe"), {})
    if not subscription.get("successful"):
        print("SUBSCRIBE_FAILED")
        return 2
    print("SUBSCRIBED /data/ChangeEvents")
    counts = {}
    total = 0
    deadline = time.monotonic() + 90
    try:
        while time.monotonic() < deadline:
            messages = send([{"channel":"/meta/connect","clientId":client_id,"connectionType":"long-polling","id":"2"}])
            for message in messages:
                if message.get("channel") == "/meta/connect" and not message.get("successful", False):
                    print("CONNECT_FAILED")
                    return 3
                if message.get("channel") != "/data/ChangeEvents":
                    continue
                header = message.get("data",{}).get("payload",{}).get("ChangeEventHeader",{})
                entity = str(header.get("entityName","UNKNOWN"))[:80]
                change_type = str(header.get("changeType","UNKNOWN"))[:40]
                counts[entity] = counts.get(entity, 0) + 1
                total += 1
                print("EVENT entity=" + entity + " type=" + change_type)
    finally:
        try:
            send([{"channel":"/meta/disconnect","clientId":client_id}],timeout=10)
        except requests.RequestException:
            pass
    print("TOTAL_EVENTS",total)
    print("COUNTS_BY_ENTITY",counts)
    if total == 0:
        print("NO_EVENTS_OBSERVED_IN_90_SECONDS")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("TEST_ERROR",type(exc).__name__)
        raise SystemExit(1)
