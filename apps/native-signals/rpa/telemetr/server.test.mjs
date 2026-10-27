import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {promises as fs} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';
import {newJob} from './contract.mjs';

test('localhost API rejects website requests and recovers interrupted jobs without opening browser',async()=>{
  const dir=await fs.mkdtemp(path.join(os.tmpdir(),'tg-rpa-test-'));
  await fs.mkdir(path.join(dir,'jobs'));
  const job=newJob({queries:[{companyID:'fixture',companyName:'Fixture',query:'fixture'}],dateFrom:'2026-10-01',dateTo:'2026-10-11',maxPages:1});
  job.state='running';await fs.writeFile(path.join(dir,'jobs',job.id+'.json'),JSON.stringify(job));
  const child=spawn(process.execPath,[fileURLToPath(new URL('./server.mjs',import.meta.url))],{env:{...process.env,TG_SIGNALS_RPA_DIR:dir},stdio:'ignore'});
  const exited = new Promise(resolve=>child.once('exit',resolve));
  try {
    let control;
    for(let i=0;i<40;i++){try{control=JSON.parse(await fs.readFile(path.join(dir,'control.json'),'utf8'));break;}catch{await delay(100);}}
    assert.ok(control);
    const url=`http://127.0.0.1:${control.port}`;
    const headers={Authorization:'Bearer '+control.token};
    assert.equal((await fetch(url+'/status')).status,403);
    assert.equal((await fetch(url+'/status',{headers:{...headers,Origin:'https://example.com'}})).status,403);
    const status=await (await fetch(url+'/status',{headers})).json();
    assert.equal(status.browserOpen,false);assert.equal(status.jobs[0].state,'paused');
    assert.equal((await fs.stat(path.join(dir,'control.json'))).mode&0o777,0o600);
    const invalid=await fetch(url+'/jobs',{method:'POST',headers,body:JSON.stringify({queries:[]})});
    assert.equal(invalid.status,400);
    const check=await (await fetch(url+'/status',{headers})).json();assert.equal(check.jobs.length,1);assert.equal(check.browserOpen,false);
  } finally {
    if (child.exitCode === null) child.kill('SIGTERM');await exited;
    await fs.rm(dir,{recursive:true,force:true});
  }
});
