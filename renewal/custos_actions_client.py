#!/usr/bin/env python3
"""Custos's proactive Signal and Automata commands. Writes read JSON on stdin.

custos-actions signal-contacts
custos-actions signal-send < {request_id,target,message}.json
custos-actions automata-status
custos-actions automata-deploy < {request_id,goal_id,commit}.json
custos-actions automata-rollback < {request_id,goal_id}.json
custos-actions status REQUEST_ID
Queue acceptance is not delivery. Reuse the exact request ID/payload on timeout.
"""
import argparse
import http.client
import json
import sys


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['signal-contacts','signal-send','automata-status','automata-deploy','automata-rollback','status'])
    p.add_argument('request_id',nargs='?'); args=p.parse_args()
    connection=None
    try:
        if args.action in ('signal-send','automata-deploy','automata-rollback'):
            raw=sys.stdin.buffer.read(32769)
            if len(raw)>32768: raise ValueError('request too large')
            payload=json.loads(raw)
            if not isinstance(payload,dict) or 'action' in payload: raise ValueError('object without action required')
        else: payload={}
        if args.action=='status':
            if not args.request_id: raise ValueError('request ID required')
            payload['request_id']=args.request_id
        elif args.request_id: raise ValueError('unexpected request ID argument')
        payload['action']=args.action
        connection=http.client.HTTPConnection('192.168.86.44',18082,timeout=40)
        connection.request('POST','/v1/actions',body=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        response=connection.getresponse(); raw=response.read(1048577)
        if len(raw)>1048576: raise ValueError('response limit')
        result=json.loads(raw); print(json.dumps(result,ensure_ascii=False))
        return 0 if response.status==200 and result.get('ok') else 1
    except (ValueError,OSError,http.client.HTTPException,RecursionError):
        print(json.dumps({'ok':False,'error':'Unavailable or invalid request; a write may be queued. Check status and reuse its exact request ID.'}))
        return 2
    finally:
        if connection: connection.close()

if __name__=='__main__': sys.exit(main())
