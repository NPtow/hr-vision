#!/usr/bin/env python3
"""Provision only ClickStack's files. Run as root on outreach; prints no secrets."""
import json
import os
from pathlib import Path
import secrets

root = Path('/etc/mashadasha-observability')
root.mkdir(mode=0o700, exist_ok=True)
os.chmod(root, 0o700)
state = Path('/var/lib/mashadasha-observability')
state.mkdir(mode=0o700, exist_ok=True)
connection = 'MashaDasha'
common = {'connection': connection, 'resourceAttributesExpression': 'ResourceAttributes'}
logs = dict(common, name='Logs', kind='log', **{'from': {'databaseName': 'default', 'tableName': 'otel_logs'}},
    timestampValueExpression='TimestampTime', displayedTimestampValueExpression='Timestamp', implicitColumnExpression='Body',
    bodyExpression='Body', serviceNameExpression='ServiceName', eventAttributesExpression='LogAttributes',
    severityTextExpression='SeverityText', traceIdExpression='TraceId', spanIdExpression='SpanId',
    defaultTableSelectExpression='Timestamp,ServiceName,SeverityText,Body', traceSourceId='Traces', metricSourceId='Metrics')
traces = dict(common, name='Traces', kind='trace', **{'from': {'databaseName': 'default', 'tableName': 'otel_traces'}},
    timestampValueExpression='Timestamp', implicitColumnExpression='SpanName', serviceNameExpression='ServiceName',
    eventAttributesExpression='SpanAttributes', traceIdExpression='TraceId', spanIdExpression='SpanId',
    durationExpression='Duration', durationPrecision=9, parentSpanIdExpression='ParentSpanId', spanNameExpression='SpanName',
    spanKindExpression='SpanKind', statusCodeExpression='StatusCode', statusMessageExpression='StatusMessage',
    defaultTableSelectExpression='Timestamp,ServiceName,StatusCode,round(Duration/1e6),SpanName',
    logSourceId='Logs', sessionSourceId='Sessions', metricSourceId='Metrics')
sessions = dict(common, name='Sessions', kind='session', **{'from': {'databaseName': 'default', 'tableName': 'hyperdx_sessions'}},
    timestampValueExpression='TimestampTime', traceSourceId='Traces')
metrics = dict(common, name='Metrics', kind='metric', **{'from': {'databaseName': 'default', 'tableName': ''}},
    timestampValueExpression='TimeUnix', logSourceId='Logs', metricTables={
    'gauge':'otel_metrics_gauge','sum':'otel_metrics_sum','histogram':'otel_metrics_histogram',
    'summary':'otel_metrics_summary','exponential histogram':'otel_metrics_exponential_histogram'})
env = root / 'app.env'
if not env.exists():
    env.write_text('EXPRESS_SESSION_SECRET=' + secrets.token_hex(32) + '\n' +
        "DEFAULT_SOURCES='" + json.dumps([logs, traces, sessions, metrics], separators=(',', ':')) + "'\n")
    os.chmod(env, 0o600)
print('ClickStack state and environment prepared')
