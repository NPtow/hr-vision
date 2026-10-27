import argparse
from .config import Settings


def main():
    parser = argparse.ArgumentParser(description='Independent Daily source service')
    sub = parser.add_subparsers(dest='command',required=True)
    serve = sub.add_parser('serve')
    serve.add_argument('--host',default='127.0.0.1')
    serve.add_argument('--port',type=int,default=8080)
    worker = sub.add_parser('worker')
    worker.add_argument('--concurrency',type=int,default=2)
    scheduler = sub.add_parser('reconcile')
    scheduler.add_argument('--once',action='store_true')
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command=='serve':
        import uvicorn
        # Access logs contain only static API paths and opaque IDs, but disable
        # default logs to avoid credential-like values in attacker-controlled paths.
        uvicorn.run('source_daily.runtime:api',factory=True,host=args.host,port=args.port,access_log=False)
    elif args.command=='worker':
        from .worker import make_celery
        app = make_celery(settings)
        if app is None:
            parser.error('SOURCE_DAILY_BROKER_URL must configure RabbitMQ')
        app.worker_main(['worker','--loglevel=WARNING','--queues='+settings.queue,
                         '--concurrency='+str(args.concurrency),'--without-gossip','--without-mingle'])
    else:
        from .worker import reconcile
        reconcile(settings,once=args.once)


if __name__=='__main__':
    main()
