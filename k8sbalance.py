#!/usr/bin/env python3

import sys

SERVICE_LABEL='spreadspace.org/onion-service'
INSTANCE_ANNOT='spreadspace.org/onion-instance'
REFRESH_INTERVAL=120
SECRETS_PATH='/var/run/secrets/spreadspace.org/onionbalance'
ONIONBALANCE_CONFIG='/tmp/onionbalance.yml'
ONIONBALANCE_CONTROL='/tmp/onionbalance.control'

def get_onion_mapping(client, NAMESPACE):
    l = client.list_namespaced_pod(NAMESPACE, label_selector=SERVICE_LABEL)
    m = {}

    for pod in l.items:
        if not pod.metadata.annotations:
            continue

        if INSTANCE_ANNOT not in pod.metadata.annotations:
            continue

        service = pod.metadata.labels[SERVICE_LABEL]
        instance = pod.metadata.annotations[INSTANCE_ANNOT]

        if service not in m:
            m[service] = set()

        m[service].add(instance)

    return m


def onionbalance_config(mapping):
    import json, os.path
    return json.dumps({
        'STATUS_SOCKET_LOCATION': ONIONBALANCE_CONTROL,
        'REFRESH_INTERVAL': REFRESH_INTERVAL,
        'services': [
            { 'key': os.path.join(SECRETS_PATH, address),
              'instances': [ {'address': s} for s in instances ]
            }
            for address, instances in mapping.items()
        ]
    })


def start_onionbalance(mapping):
    from subprocess import Popen

    with open(ONIONBALANCE_CONFIG, 'w') as fd:
        fd.write(onionbalance_config(mapping))
    return Popen(['onionbalance', '-c', ONIONBALANCE_CONFIG])


def kill(process):
    from subprocess import TimeoutExpired
    print('Sending SIGTERM to onionbalance')
    process.terminate()

    try:
        process.wait(timeout=5)
    except TimeoutExpired:
        print('Onionbalance failed to terminate within 5s')
        process.kill()
        process.wait(timeout=60)


def log_changes(oldmap, newmap, output=sys.stderr):
    output.write('Updating onionbalance config:\n')
    for host in set(itertools.chain(newmap.keys(), oldmap.keys())):
        if host in newmap and host in oldmap and newmap[host] == oldmap[host]:
            continue

        output.write('  %s\n' % host)
        output.write('    Adding: %s\n' % (newmap[host] - oldmap[host]))
        output.write('    Removing: %s\n' % (oldmap[host] - newmap[host]))
        output.write('    Keeping: %s\n' % (oldmap[host] & newmap[host]))
        output.flush()


if __name__ == '__main__':
    import itertools, os
    from kubernetes import client, config, watch
    NAMESPACE = os.environ['POD_NAMESPACE']

    config.incluster_config.load_incluster_config()
    v1 = client.CoreV1Api()

    onionmap     = get_onion_mapping(v1, NAMESPACE)
    onionbalance = start_onionbalance(onionmap)

    stream = watch.Watch().stream(v1.list_namespaced_pod,
                                  NAMESPACE,
                                  label_selector=SERVICE_LABEL
    )

    for event in stream:
        newmap = get_onion_mapping(v1, NAMESPACE)
        if newmap == onionmap:
            continue

        log_changes(onionmap, newmap)
        onionmap = newmap
        kill(onionbalance)
        onionbalance = start_onionbalance(onionmap)
