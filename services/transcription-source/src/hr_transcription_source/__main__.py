import argparse
import json

from .settings import Settings


def main():
    parser=argparse.ArgumentParser(description='Independent transcription source coordinator')
    parser.add_argument('command',choices=['api','worker','scheduler','run-once','reconcile'])
    parser.add_argument('--port',type=int,default=8093)
    parser.add_argument('--job-id')
    args=parser.parse_args()
    settings=Settings.from_env()
    settings.data_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
    if args.command=='api':
        import uvicorn
        from .api import create_app
        uvicorn.run(create_app(settings),host='127.0.0.1',port=args.port)
    elif args.command in ('worker','scheduler'):
        from .queue import build_celery
        app=build_celery(settings,worker_signals=args.command=='worker')
        if args.command=='worker':
            app.worker_main(['worker','--pool=solo','--concurrency=1','-Q','hr_transcription_source','--without-gossip','--without-mingle','--without-heartbeat','--loglevel=INFO'])
        else:
            app.start(['beat','--loglevel=INFO','--schedule='+str(settings.data_dir/'celerybeat')])
    else:
        from .worker import Runtime
        runtime=Runtime(settings)
        print(json.dumps(runtime.run_once(args.job_id) if args.command=='run-once' else runtime.reconcile()))

if __name__=='__main__':
    main()
