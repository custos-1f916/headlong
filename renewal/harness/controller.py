"""Host-owned serialized deployment state machine. Candidate code never runs here."""
import json,pathlib,re,threading,time,traceback
from harness.common import atomic_json,HEX
P=pathlib.Path
TERMINAL={'qualified','committed','rolled-back','failed','paused-rolled-back'}
ACTIVE={'draining','stopped','switching','verifying','rolling-back'}

class Controller:
 def __init__(self,state,adapter):
  self.state=P(state);self.state.mkdir(parents=True,exist_ok=True);self.adapter=adapter;self.lock=threading.RLock()
  (self.state/'requests').mkdir(exist_ok=True)
 def read(self,p,default=None):
  try:return json.loads(P(p).read_text())
  except FileNotFoundError:return default
 def current(self):return self.read(self.state/'current.json',{})
 def records(self):return [self.read(p) for p in sorted((self.state/'requests').glob('*.json'))]
 def save(self,row,phase,**fields):
  row.update(fields,phase=phase,updated=time.time());row.setdefault('history',[]).append({'phase':phase,'at':row['updated']})
  atomic_json(self.state/'requests'/(row['request_id']+'.json'),row)
 def handle(self,payload):
  with self.lock:
   if not isinstance(payload,dict):raise ValueError('JSON object required')
   action=payload.get('action')
   if action=='current':return {'ok':True,'current':self.current(),'controller':self.adapter.version}
   if action=='status':
    rid=payload.get('request_id')
    if rid is None:return {'ok':True,'current':self.current(),'requests':self.records()[-20:]}
    if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',rid):raise ValueError('invalid request_id')
    row=self.read(self.state/'requests'/(rid+'.json'))
    return {'ok':row is not None,'request':row}
   if action not in {'qualify','deploy','rollback'}:raise ValueError('unknown harness action')
   allowed={'action','artifact','request_id'}|({'expected_current'} if action!='qualify' else set())
   if set(payload)!=allowed:raise ValueError('unexpected or missing request fields')
   if not isinstance(payload['artifact'],str) or not HEX.fullmatch(payload['artifact']):raise ValueError('invalid artifact')
   rid=payload['request_id']
   if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',rid):raise ValueError('invalid request_id')
   if action!='qualify' and (not isinstance(payload['expected_current'],str) or not HEX.fullmatch(payload['expected_current'])):raise ValueError('invalid expected_current')
   old=self.read(self.state/'requests'/(rid+'.json'))
   if old:
    if old['payload']!=payload:raise ValueError('request_id already used with different payload')
    return {'ok':True,'request':old,'replayed':True}
   if sum(r['phase'] not in TERMINAL for r in self.records())>=10:raise ValueError('request queue full')
   row={'request_id':rid,'payload':payload,'created':time.time(),'controller':self.adapter.version}
   self.save(row,'accepted');return {'ok':True,'request':row}
 def recover(self):
  for row in self.records():
   if row['phase']=='draining':
    self.adapter.abort_drain(row);self.save(row,'failed',error='interrupted drain');continue
   if row['phase'] in ACTIVE:
    # Intent is persisted before any live mutation. Recovery always has the
    # externally retained previous artifact and never restores old identity data.
    self.rollback(row,'controller interrupted during '+row['phase'])
 def rollback(self,row,error):
  previous=row.get('previous')
  if not previous:raise RuntimeError('missing external rollback record')
  self.save(row,'rolling-back',error=error)
  try:
   self.adapter.restore(previous['artifact'],row)
   atomic_json(self.state/'current.json',previous)
   self.save(row,'paused-rolled-back' if self.adapter.paused() else 'rolled-back',current=previous)
  except Exception as e:
   # Keep an active recovery phase so restart retries. Do not label a failed
   # restoration successful or release the maintenance gate.
   self.save(row,'rolling-back',recovery_error=str(e)[:500]);raise
 def process_one(self):
  with self.lock:
   rows=[r for r in self.records() if r['phase'] not in TERMINAL]
   if not rows:return False
   row=min(rows,key=lambda r:r['created'])
  p=row['payload'];artifact=p['artifact']
  try:
   if row['phase']=='draining':
    self.adapter.abort_drain(row);self.save(row,'failed',error='controller interrupted while draining; previous release retained');return True
   if row['phase'] in ACTIVE:self.rollback(row,'recovering interrupted operation');return True
   if p['action']=='qualify':
    self.save(row,'qualifying');evidence=self.adapter.qualify(artifact)
    self.save(row,'qualified',evidence=evidence);return True
   current=self.current()
   if current.get('artifact')!=p['expected_current']:raise ValueError('current release changed; inspect and submit a new request')
   qualified=next((r for r in self.records() if r['phase']=='qualified' and r['payload']['artifact']==artifact and r['controller']==self.adapter.version),None)
   if not qualified:raise ValueError('exact artifact lacks qualification by current controller')
   if self.adapter.paused():raise ValueError('operator pause is active')
   self.adapter.preflight(artifact,current,qualified['evidence'])
   self.save(row,'draining',previous=current)
   self.adapter.drain(row)
   self.save(row,'stopped');self.adapter.stop(row)
   self.save(row,'switching');manifest=self.adapter.switch(artifact,row)
   self.save(row,'verifying');self.adapter.verify(artifact,row)
   if self.adapter.paused():raise RuntimeError('operator paused during rollout')
   selected={'artifact':artifact,'commit':manifest['commit'],'previous':current['artifact'],'request_id':row['request_id'],'installed':time.time()}
   # Publishing current before terminal receipt is safe: an interrupted verify
   # rolls back using row.previous. A duplicate request never starts again.
   atomic_json(self.state/'current.json',selected)
   self.save(row,'committed',current=selected)
  except Exception as e:
   if row['phase']=='draining':
    self.adapter.abort_drain(row);self.save(row,'failed',error=str(e)[:500])
   elif row['phase'] in ACTIVE:self.rollback(row,str(e)[:500])
   else:self.save(row,'failed',error=str(e)[:500])
  return True
