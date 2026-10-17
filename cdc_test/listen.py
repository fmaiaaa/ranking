"""Bounded, read-only CDC observer. Never log payloads or record identifiers."""
import json
import os
import re
import time
from collections import Counter

import requests

CHANNEL = '/data/ChangeEvents'


def observe(send, seconds=120, replay=-1, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + seconds
    counts = Counter()
    seen = set()
    client = None
    cursor = replay
    advice = {}
    polls = timeouts = reconnects = 0
    connected = False

    def consume(messages):
        nonlocal cursor
        for message in messages:
            if message.get('channel') != CHANNEL:
                continue
            data = message.get('data', {})
            header = data.get('payload', {}).get('ChangeEventHeader', {})
            entity = header.get('entityName', '')
            kind = header.get('changeType', '')
            # Only known metadata shape qualifies as an actual CDC event.
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', str(entity)):
                continue
            if kind not in ('CREATE', 'UPDATE', 'DELETE', 'UNDELETE', 'GAP_CREATE', 'GAP_UPDATE', 'GAP_DELETE', 'GAP_UNDELETE', 'GAP_OVERFLOW'):
                continue
            event_id = data.get('event', {}).get('replayId')
            if event_id is not None:
                cursor = event_id
                key = str(event_id)
                if key in seen:
                    continue
                seen.add(key)
            counts[entity] += 1

    try:
        while clock() < deadline:
            if client is None:
                hello = send([{'channel': '/meta/handshake', 'version': '1.0',
                               'minimumVersion': '1.0', 'supportedConnectionTypes': ['long-polling'],
                               'ext': {'replay': True}}], timeout=(10, 30))
                ack = next((m for m in hello if m.get('channel') == '/meta/handshake'), {})
                if not ack.get('successful'):
                    raise RuntimeError('HANDSHAKE_FAILED')
                client = ack['clientId']
                advice = ack.get('advice', {})
                subscribed = send([{'channel': '/meta/subscribe', 'clientId': client,
                                    'subscription': CHANNEL, 'ext': {'replay': {CHANNEL: cursor}}}], timeout=(10, 30))
                ack = next((m for m in subscribed if m.get('channel') == '/meta/subscribe'), {})
                if not ack.get('successful'):
                    raise RuntimeError('SUBSCRIBE_FAILED')
                consume(subscribed)
                print('SUBSCRIBED', CHANNEL, 'mode=' + ('retained' if replay == -2 else 'live'), flush=True)
            remaining = deadline - clock()
            if remaining <= 0:
                break
            # Request a server hold shorter than the HTTP timeout and remaining window.
            hold = min(110, max(0, remaining - 5), max(0, advice.get('timeout', 110000) / 1000))
            try:
                messages = send([{'channel': '/meta/connect', 'clientId': client,
                                  'connectionType': 'long-polling', 'advice': {'timeout': int(hold * 1000)}}],
                                timeout=(min(10, remaining), min(hold + 15, remaining)))
            except requests.ReadTimeout:
                timeouts += 1
                print('POLL_READ_TIMEOUT retrying_within_window', flush=True)
                continue
            polls += 1
            consume(messages)
            ack = next((m for m in messages if m.get('channel') == '/meta/connect'), {})
            advice.update(ack.get('advice', {}))
            action = advice.get('reconnect', 'retry')
            if action == 'none':
                raise RuntimeError('SERVER_STOPPED_CONNECTION')
            if action == 'handshake' or str(ack.get('error', '')).startswith('403::Unknown client'):
                client = None
                reconnects += 1
                continue
            if not ack.get('successful'):
                raise RuntimeError('CONNECT_FAILED')
            connected = True
            sleep(min(max(0.05, advice.get('interval', 0) / 1000), max(0, deadline - clock())))
    finally:
        if client:
            try:
                send([{'channel': '/meta/disconnect', 'clientId': client}], timeout=(5, 5))
            except requests.RequestException:
                pass
    total = sum(counts.values())
    result = {'mode': 'retained' if replay == -2 else 'live', 'total_events': total,
              'counts_by_entity': dict(sorted(counts.items())), 'successful_polls': polls,
              'read_timeouts': timeouts, 'rehandshakes': reconnects,
              'status': 'EVENTS_CONFIRMED' if total else ('NO_EVENTS_OBSERVED' if connected else 'TRANSPORT_BLOCKED')}
    print('CDC_RESULT', json.dumps(result, sort_keys=True), flush=True)
    return 0 if connected or total else 3


def main():
    from simple_salesforce import Salesforce
    sf = Salesforce(username=os.environ['SALESFORCE_USER'], password=os.environ['SALESFORCE_PASSWORD'],
                    security_token=os.environ['SALESFORCE_TOKEN'], domain=os.getenv('SALESFORCE_DOMAIN') or 'login')
    seconds = int(os.getenv('CDC_SECONDS', '120'))
    replay = int(os.getenv('CDC_REPLAY', '-1'))
    if not 10 <= seconds <= 300 or replay not in (-1, -2):
        raise ValueError('INVALID_TEST_CONFIGURATION')
    with requests.Session() as session:
        session.headers.update({'Authorization': 'Bearer ' + sf.session_id, 'Content-Type': 'application/json'})
        def send(messages, timeout):
            response = session.post('https://' + sf.sf_instance + '/cometd/60.0', json=messages, timeout=timeout)
            response.raise_for_status()
            return response.json()
        return observe(send, seconds, replay)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('TEST_ERROR', type(exc).__name__, flush=True)
        raise SystemExit(1)
