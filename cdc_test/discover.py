"""Read-only Salesforce CDC channel discovery; never prints record payloads."""
import os
import requests
from simple_salesforce import Salesforce

def main():
    sf = Salesforce(username=os.environ["SALESFORCE_USER"],
                    password=os.environ["SALESFORCE_PASSWORD"],
                    security_token=os.environ["SALESFORCE_TOKEN"],
                    domain=os.getenv("SALESFORCE_DOMAIN") or "login")
    base = "https://" + sf.sf_instance
    headers = {"Authorization": "Bearer " + sf.session_id}
    print("AUTH_OK")
    query = "SELECT COUNT() FROM PlatformEventChannelMember WHERE EventChannel = 'ChangeEvents'"
    r = requests.get(base + "/services/data/v60.0/tooling/query",
                     params={"q": query}, headers=headers, timeout=25)
    if r.ok:
        print("DEFAULT_CDC_SELECTED_ENTITY_COUNT", r.json()["totalSize"])
    else:
        print("DEFAULT_CDC_SELECTION_UNAVAILABLE HTTP", r.status_code)
    for api, query in [
        ("tooling", "SELECT DeveloperName, MasterLabel FROM PlatformEventChannel LIMIT 100"),
        ("tooling", "SELECT DeveloperName, EventChannel FROM PlatformEventChannelMember LIMIT 100"),
    ]:
        try:
            r = requests.get(base + "/services/data/v60.0/" + api + "/query",
                             params={"q": query}, headers=headers, timeout=25)
            print("DISCOVERY", query.split(" FROM ")[1].split(" ")[0], "HTTP", r.status_code)
            if r.ok:
                data = r.json()
                for item in data.get("records", [])[:100]:
                    print("CHANNEL_METADATA", {k:v for k,v in item.items()
                          if k in ("DeveloperName","MasterLabel","EventChannel")})
            else:
                print("DISCOVERY_UNAVAILABLE (may require additional metadata permissions)")
        except requests.RequestException:
            print("DISCOVERY_NETWORK_ERROR")
    endpoint = base + "/cometd/60.0"
    headers["Content-Type"] = "application/json"
    s = requests.Session()
    def post(messages):
        r = s.post(endpoint, headers=headers, json=messages, timeout=30)
        r.raise_for_status()
        return r.json()
    handshake = post([{"channel":"/meta/handshake","version":"1.0",
                       "minimumVersion":"1.0","supportedConnectionTypes":["long-polling"]}])
    hello = next((x for x in handshake if x.get("channel")=="/meta/handshake"), {})
    if not hello.get("successful"):
        print("HANDSHAKE_FAILED")
        return
    print("HANDSHAKE_OK")
    client = hello["clientId"]
    for i, topic in enumerate(["/data/ChangeEvents","/data/OpportunityChangeEvent",
                               "/data/EventChangeEvent",
                               "/data/Avaliacao_credito__ChangeEvent"]):
        result = post([{"channel":"/meta/subscribe","clientId":client,
                        "subscription":topic,"id":str(i+1)}])
        ack = next((x for x in result if x.get("channel")=="/meta/subscribe"), {})
        print("PROBE",topic,"OK" if ack.get("successful") else "FAILED",
              str(ack.get("error",""))[:120])
    try:
        post([{"channel":"/meta/disconnect","clientId":client}])
    except requests.RequestException:
        pass

if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        print("DIAGNOSTIC_ERROR",type(exc).__name__)
        raise SystemExit(1)
