"""Explicit entrypoints; no application starts on import."""
import argparse
import json
import os


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(prog='md-transcription')
    sub = parser.add_subparsers(dest='command', required=True)
    api = sub.add_parser('api')
    api.add_argument('--host', default='127.0.0.1')
    api.add_argument('--port', type=int, default=8080)
    once = sub.add_parser('run-once')
    once.add_argument('--job-id')
    sub.add_parser('reconcile')
    sub.add_parser('worker')
    sub.add_parser('beat')
    args = parser.parse_args()
    from .settings import Settings
    settings = Settings.from_env()
    if args.command == 'api':
        import uvicorn
        from .api import create_app
        uvicorn.run(create_app(settings), host=args.host, port=args.port, access_log=False)
    elif args.command in ('worker', 'beat'):
        if not settings.broker_url:
            parser.error('TRANSCRIPTION_BROKER_URL is required')
        from .queue import build_celery
        app = build_celery(settings)
        if args.command == 'worker':
            app.worker_main(['worker', '--loglevel=WARNING', '--concurrency=1', '--without-gossip', '--without-mingle'])
        else:
            app.start(['beat', '--loglevel=WARNING', '--schedule=' + str(settings.data_dir / 'celerybeat')])
    else:
        from .worker import Runtime
        runtime = Runtime(settings)
        result = runtime.reconcile() if args.command == 'reconcile' else runtime.run_once(args.job_id)
        print(json.dumps(result))


if __name__ == '__main__':
    main()
