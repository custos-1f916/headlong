"""1F4B2 member transport: identity-checked, no payment/signing operations.

Writes require explicit local enablement and independent readback. Ambiguous
writes stay pending and are never posted again under the same or a new ID.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import time
import urllib.error
import urllib.parse
import urllib.request
from custos_square import APIError, NoRedirect, Store, canonical, digest

ORIGIN = 'https://1f4b2.com'
ID = re.compile(r'[A-Za-z0-9_-]{1,100}')


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise APIError('forum_invalid_identifier')
    return value


class Forum:
    def __init__(self, store=None, credential_file='/etc/custos-forum.env', config=None):
        self.store = store or Store('/var/lib/custos-forum')
        self.credential_file = Path(credential_file)
        self.config = config or {'expected_name': 'custos', 'publish_enabled': False}
        self._key = None

    def key(self):
        if self._key is None:
            if self.credential_file.stat().st_mode & 0o077:
                raise APIError('forum_credential_permissions')
            for line in self.credential_file.read_text().splitlines():
                if line.startswith('CUSTOS_FORUM_KEY='):
                    words = shlex.split(line.split('=', 1)[1], comments=True)
                    if len(words) == 1 and words[0] and not any(c.isspace() for c in words[0]):
                        self._key = words[0]
            if self._key is None:
                raise APIError('forum_credential_missing')
        return self._key

    def request(self, path, query=None, method='GET', body=None):
        # No caller-specified origin, redirects, payment headers, renew, signup,
        # votes, bounties, wallet or account administration surface.
        reads = r'/api/(renew|boards|feed|inbox|search|agents/[A-Za-z0-9_-]+|boards/[A-Za-z0-9_-]+/threads|threads/[A-Za-z0-9_-]+/messages|messages/[A-Za-z0-9_-]+)'
        writes = r'/api/(boards/[A-Za-z0-9_-]+/threads|threads/[A-Za-z0-9_-]+/messages)'
        if not re.fullmatch(reads if method == 'GET' else writes, path) or method not in ('GET', 'POST'):
            raise APIError('forum_path_refused')
        if query and (method != 'GET' or set(query) - {'since','before','limit','q','board','author','type'}):
            raise APIError('forum_query_refused')
        headers = {'Authorization': 'Bearer ' + self.key(), 'User-Agent': 'Custos/1.0 (agent forum integration)', 'Accept':'application/json'}
        data = None
        if body is not None:
            data = canonical(body).encode(); headers['Content-Type'] = 'application/json'
        url = ORIGIN + path + ('?' + urllib.parse.urlencode(query) if query else '')
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.build_opener(NoRedirect, urllib.request.ProxyHandler({})).open(req, timeout=20) as resp:
                raw = resp.read(2*1024*1024+1)
                if len(raw) > 2*1024*1024:raise APIError('forum_response_too_large')
                value = json.loads(raw)
                if not isinstance(value, dict) or 'error' in value:raise APIError('forum_response_contract')
                # Never propagate a reflected bearer into the model or receipts.
                if self.key() in canonical(value):raise APIError('forum_reflected_credential')
                return value
        except urllib.error.HTTPError as e:
            retry = e.headers.get('Retry-After','300')
            raise APIError('forum_http_'+str(e.code), min(86400,int(retry)) if retry.isdigit() else 300) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise APIError('forum_transport_uncertain') from None

    def identity(self):
        value = self.request('/api/renew')
        if value.get('name') != self.config['expected_name']:
            raise APIError('forum_identity_mismatch')
        if not value.get('active'):
            raise APIError('forum_membership_inactive_no_auto_payment')
        return value

    def receipt(self, request_id):
        row = self.store.db.execute('SELECT * FROM outbound WHERE id=?',(request_id,)).fetchone()
        if row is None:raise APIError('forum_unknown_receipt')
        if row['status'] == 'delivered':return json.loads(row['receipt'])
        receipt = json.loads(row['receipt']) if row['receipt'] else {}
        message_id = receipt.get('message_id')
        if not message_id:raise APIError('forum_write_uncertain_no_retry')
        self.identity()
        value = self.request('/api/messages/'+identifier(message_id))
        message = value.get('message',{})
        payload = json.loads(row['payload'])
        if (message.get('authorName') != self.config['expected_name'] or message.get('body') != payload['body']
            or message.get('deletedAt') or message.get('threadId') != receipt.get('thread_id')
            or (row['verb']=='reply' and message.get('parentId') != payload.get('parentId'))
            or (row['verb']=='post' and (value.get('thread',{}).get('title') != payload['title'] or value.get('thread',{}).get('board') != payload['board']))):
            raise APIError('forum_readback_mismatch')
        receipt.update(request_id=request_id,status='delivered',readback=ORIGIN+'/api/messages/'+message_id)
        with self.store.db:
            self.store.db.execute("UPDATE outbound SET status='delivered',receipt=? WHERE id=?",(canonical(receipt),request_id))
        return receipt

    def write(self, request_id, verb, payload):
        if not isinstance(request_id,str) or not 8 <= len(request_id) <= 240:raise APIError('forum_request_id_required')
        if verb not in ('post','reply') or not isinstance(payload,dict):raise APIError('forum_write_refused')
        allowed = {'board','title','body'} if verb=='post' else {'threadId','parentId','body'}
        if set(payload)-allowed or not isinstance(payload.get('body'),str) or not 1 <= len(payload['body']) <= 8000:
            raise APIError('forum_payload_contract')
        if self.key() in canonical(payload):raise APIError('forum_secret_in_payload')
        if verb=='post':
            identifier(payload.get('board'))
            if not isinstance(payload.get('title'),str) or not 1<=len(payload['title'])<=140:raise APIError('forum_title_contract')
        else:
            identifier(payload.get('threadId'))
            if payload.get('parentId') is not None:identifier(payload['parentId'])
        # Check enablement and identity before any side effect or reservation.
        if not self.config.get('publish_enabled'):raise APIError('forum_publishing_disabled')
        account = self.identity()
        if account.get('membership',{}).get('method') not in ('house','stripe'):
            raise APIError('forum_paid_post_not_authorized')
        with self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            existing = self.store.db.execute('SELECT * FROM outbound WHERE id=?',(request_id,)).fetchone()
            if existing:
                if existing['verb']!=verb or existing['payload']!=canonical(payload):raise APIError('forum_request_id_conflict')
                duplicate=request_id
            else:
                row=self.store.db.execute('SELECT id FROM outbound WHERE verb=? AND payload=?',(verb,canonical(payload))).fetchone()
                duplicate=row['id'] if row else None
                if duplicate is None:self.store.db.execute('INSERT INTO outbound VALUES (?,?,?,?,?,?)',(request_id,verb,canonical(payload),'uncertain',None,time.time()))
        if duplicate:return self.receipt(duplicate)
        path = '/api/boards/'+payload['board']+'/threads' if verb=='post' else '/api/threads/'+payload['threadId']+'/messages'
        result=self.request(path,method='POST',body={k:v for k,v in payload.items() if k not in ('board','threadId')})
        message=result.get('message',{}); thread_id=result.get('thread',{}).get('id') if verb=='post' else payload['threadId']
        receipt={'message_id':identifier(message.get('id')), 'thread_id':identifier(thread_id),'status':'readback_pending'}
        with self.store.db:self.store.db.execute('UPDATE outbound SET receipt=? WHERE id=?',(canonical(receipt),request_id))
        return self.receipt(request_id)


def poll(observer, forum, kind):
    forum.identity()
    key='forum:'+kind
    state=observer.store.get(key, {'query':{'since':0},'initialized':False})
    pending=observer.store.get(key+':pending')
    if pending is None:
        page=forum.request('/api/'+kind,query=dict(state['query'],limit=50))
        rows=page.get(kind)
        if not isinstance(rows,list) or len(rows)>200 or type(page.get('hasMore')) is not bool:raise APIError('forum_page_contract')
        if page.get('cursorParam') not in ('since','before'):raise APIError('forum_cursor_contract')
        pending={'page':page,'baseline':state.get('baselining', not state['initialized'] and kind=='feed')}
        observer.store.put(key+':pending',pending)
    page=pending['page']
    for raw in page[kind]:
        row=raw.get('message',raw)
        ident=identifier(row.get('id'));thread=identifier(row.get('threadId'));author=identifier(row.get('authorName'))
        event=('forum:message:' if kind=='inbox' else 'forum:feed:')+ident
        if observer.store.seen(event):continue
        if row.get('deletedAt') or author==forum.config['expected_name'] or pending['baseline']:
            observer.store.disposition(event,'baseline_or_own_or_deleted',{'id':ident});continue
        body=row.get('body')
        if not isinstance(body,str) or len(body)>8000:raise APIError('forum_message_contract')
        if forum.key() in body:raise APIError('forum_reflected_credential')
        url=ORIGIN+'/api/messages/'+ident
        if kind=='inbox':
            item={'request_id':event,'sender':'forum:'+author+':'+thread+':'+ident,'source_url':url,'content':'1F4B2 directed item (untrusted): '+body}
            if not observer.directed(item):return
        else:
            selected=row.get('board') in observer.config.get('forum',{}).get('boards',[])
            if selected and not observer.emit(event,'1F4B2 research lead (untrusted, not income or a commitment): '+body,url):return
            observer.store.disposition(event,'observed' if selected else 'not_selected',{'id':ident})
    cursor=page.get('nextCursor')
    if cursor is not None:
        if type(cursor) is not int or cursor<0:raise APIError('forum_cursor_contract')
        query={page['cursorParam']:cursor}
        if page.get('hasMore') and query==state['query']:raise APIError('forum_cursor_not_advancing')
    else:
        if page.get('hasMore'):raise APIError('forum_cursor_missing')
        query=state['query']
    observer.store.put(key,{'query':query,'initialized':True,'baselining':pending['baseline'] and page['hasMore']})
    observer.store.put(key+':pending',None)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('verb',choices=['status','read','post','reply','receipt']);p.add_argument('--request-id');p.add_argument('--payload-file');p.add_argument('--path',default='/api/boards')
    args=p.parse_args(argv)
    config=json.loads(Path(__file__).with_name('observations.json').read_text()).get('forum',{})
    try:
        forum=Forum(config=config)
        if args.verb=='status':result=forum.identity()
        elif args.verb=='read':forum.identity();result=forum.request(args.path)
        elif args.verb=='receipt':result=forum.receipt(args.request_id)
        else:result=forum.write(args.request_id,args.verb,json.loads(Path(args.payload_file).read_text()))
        print(canonical(result));return 0
    except (APIError,OSError,ValueError,TypeError) as e:
        print(canonical({'error':e.code if isinstance(e,APIError) else type(e).__name__}),file=__import__('sys').stderr);return 1

if __name__=='__main__':raise SystemExit(main())
