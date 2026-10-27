#!/usr/bin/env python3
"""Read hand-reviewed public URLs for empty-company recovery. No live writes."""
import importlib.util, json, pathlib, concurrent.futures, requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUN = ROOT / 'output/segments-2026-10-14'
OUT = RUN / 'recovery'
spec = importlib.util.spec_from_file_location('public_collector', ROOT/'scripts/segment-public-research.py')
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

def main():
    companies = {x['id']: x for x in json.loads((RUN/'companies.json').read_text())}
    plan = json.loads((OUT/'plan.json').read_text())
    fetcher = mod.Fetcher(OUT)
    def run(item):
        company = companies[item['companyID']]
        record = dict(version=1, companyID=company['id'], companyName=company['name'], searchedAt=mod.now(),
                      documents=[], contacts=[], channels=[], outboundLinks=[], jobPostings=[], checks=[], notes=[],
                      collector='segment-recovery/1', segment=company['segment'], identityEvidence=item.get('identityEvidence',[]))
        session = requests.Session()
        session.headers.update({'User-Agent':'Mozilla/5.0 (compatible; CompanyResearch/1.0; public company contacts)', 'Accept':'text/html,application/xhtml+xml'})
        stop_hosts = set()
        for url in item.get('urls', []):
            if mod.host(url) in stop_hosts: continue
            page=fetcher.fetch(url,session)
            check={'url':url,'status':page.get('error','read'),'receiptID':page.get('receiptID')}
            record['checks'].append(check)
            if page.get('error'):
                if page['error'] in ('http401','http402','http403','http429','SSLError'): stop_hosts.add(mod.host(url))
                continue
            # A verified site may redirect within its own domain. Other redirects need manual review.
            if not mod.same_site(page['url'],url) and page['url'] not in item.get('reviewedRedirects',[]):
                check['status']='externalRedirectUnverified';check['redirectURL']=page['url'];continue
            extracted=mod.extract_page(company,page)
            if extracted.get('error'):
                check['status']=extracted['error'];continue
            text=extracted['document']['text']
            if 'KillBot user verification' in page['html'] or 'gorizontal-vertikal' in page['html'] and len(text)<100:
                check['status']='accessChallenge';stop_hosts.add(mod.host(url));continue
            if any(text==d['text'] for d in record['documents']):
                check['status']='duplicateText';continue
            official=bool(item.get('official',False))
            extracted['document']['official']=official
            extracted['document']['provider']='Официальный сайт · восстановление источника' if official else 'Публичный сайт · связь проверяется'
            raw_date=extracted['document'].get('publishedAt')
            if raw_date:
                extracted['document']['rawPublishedAt']=raw_date
                extracted['document']['publishedAt']=mod.normalized_date(raw_date)
            record['documents'].append(extracted['document'])
            for field in ('contacts','channels','outboundLinks','jobPostings'):
                if not official and field in ('contacts','channels'): continue
                for value in extracted[field]:
                    value['receiptID']=page['receiptID']
                    record[field].append(value)
        for field in ('contacts','channels','outboundLinks'):
            record[field]=list({(x['url'].lower(),x.get('sourceURL'),x.get('purpose')):x for x in record[field]}.values())
        record['notes']=['Публичное восстановление пропущенного источника. Дата загрузки не является датой публикации. Рабочий адрес не подтверждает личность или право принимать решения.',*item.get('notes',[])]
        record['validationErrors']=mod.validate_record(record)
        record['statistics']={'documents':len(record['documents']),'uniqueWorkRoutes':len({x['url'].lower() for x in record['contacts']}),'contactEvidenceRows':len(record['contacts'])}
        mod.save(OUT/'companies'/(company['id']+'.json'),record)
        return {**record['statistics'],'companyID':company['id'],'checks':record['checks'],'errors':record['validationErrors']}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(run,plan))
    mod.save(OUT/'fetch-report.json',{'collectedAt':mod.now(),'companies':results})
    print(json.dumps(results,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
