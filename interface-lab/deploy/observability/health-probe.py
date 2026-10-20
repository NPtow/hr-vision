#!/usr/bin/env python3
"""Read-only service state probes. No journal bodies, process args or environment."""
import json
from pathlib import Path
import subprocess
import time
import urllib.request

UNITS = ['hr-vision-api','hr-vision-web','md-card','md-gen','md-mail','md-plan','md-tg',
         'hh-worker@nikita','hh-tgrelay','hhleads','tgleads','voprosy','postgresql@16-main','redis-server']
ROOT = Path('/var/lib/mashadasha-observability')
client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
now = str(time.time_ns())
attributes = lambda values: [{'key':key,'value':{'stringValue':str(value)}} for key,value in values.items()]
states = {}
for unit in UNITS:
    result = subprocess.run(['systemctl','is-active',unit+'.service'],capture_output=True,text=True,timeout=3)
    states[unit] = result.returncode == 0
try:
    with client.open('http://127.0.0.1:8391/api/hr/health',timeout=3) as response:
        states['hr-api-http'] = response.status == 200 and json.loads(response.read(4096)).get('ok') is True
except Exception:
    states['hr-api-http'] = False
try:
    req=urllib.request.Request('http://127.0.0.1:8381/iframe.html?id=hr-vision-product--start',headers={'Host':'hr-vision.158-160-179-53.sslip.io'})
    with client.open(req,timeout=3) as response:
        states['hr-web-http'] = response.status == 200 and b'<!doctype html' in response.read(1024).lower()
except Exception:
    states['hr-web-http'] = False
states['telemetry-disk-reserve'] = not (ROOT/'disk-paused').exists()
resource = {'attributes':attributes({'service.name':'mashadasha-service-health','deployment.environment':'production'})}
points = [{'attributes':attributes({'service.unit':unit}),'timeUnixNano':now,'asInt':'1' if up else '0'} for unit,up in states.items()]
metrics = {'resourceMetrics':[{'resource':resource,'scopeMetrics':[{'scope':{'name':'mashadasha.probes'},'metrics':[
    {'name':'mashadasha.service.up','description':'1 when the local service check succeeds, otherwise 0','unit':'1','gauge':{'dataPoints':points}}]}]}]}
previous = json.loads((ROOT/'health-state.json').read_text()) if (ROOT/'health-state.json').exists() else {}
logs = [{'timeUnixNano':now,'severityNumber':9 if up else 17,'severityText':'INFO' if up else 'ERROR',
         'body':{'stringValue':'Service recovered' if up else 'Service check failed'},
         'attributes':attributes({'service.unit':unit})} for unit,up in states.items() if previous.get(unit) != up]
for kind,payload in [('metrics',metrics),('logs',{'resourceLogs':[{'resource':resource,'scopeLogs':[{'scope':{'name':'mashadasha.probes'},'logRecords':logs}]}]})]:
    if kind == 'logs' and not logs: continue
    try:
        req=urllib.request.Request('http://127.0.0.1:8418/v1/'+kind,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        with client.open(req,timeout=3) as response: response.read(1024)
    except Exception:
        # Next cycle retries transitions; never log private HTTP bodies.
        raise SystemExit('Telemetry collector unavailable')
(ROOT/'health-state.json').write_text(json.dumps(states)+'\n')
