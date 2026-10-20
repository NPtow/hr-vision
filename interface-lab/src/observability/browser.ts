import Rum, { INSTRUMENTATIONS_ALL_DISABLED } from '@hyperdx/otel-web';
import Recorder from '@hyperdx/otel-web-session-recorder';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';
import type { ReadableSpan, SpanExporter } from '@opentelemetry/sdk-trace-base';
import { safeAttributes, safeStack, sanitizeReplay } from './privacy';

declare const __HR_RELEASE__: string;
const hosts = new Set(['mashadasha.tech', 'hr-vision.158-160-179-53.sslip.io']);

// The exporter wrapper is deliberate: otel-web 0.20.0 documents an attribute
// callback but its default OTLP exporter does not invoke that callback.
function privacyExporter(url: string): SpanExporter {
  const exporter = new OTLPTraceExporter({ url, timeoutMillis: 3000 });
  return {
    export(spans, done) {
      const safe = spans.map(span => {
        const copy = Object.create(span) as ReadableSpan;
        Object.defineProperties(copy, {
          name: {value: span.events.some(event => event.name === 'exception') ? 'browser.error' : /^(HTTP (GET|POST|PUT|PATCH|DELETE|OPTIONS)|documentLoad|record init|browser.error|browser.navigation)$/.test(span.name) ? span.name : 'browser.event'},
          attributes: {value: safeAttributes(span.attributes, location.origin)},
          events: {value: span.events.map(event => ({name: event.name === 'exception' ? 'exception' : 'browser.event', time: event.time,
            attributes: safeAttributes(event.attributes ?? {}, location.origin)}))},
          links: {value: []},
          status: {value: {code: span.status.code}},
        });
        return copy;
      });
      exporter.export(safe, done);
    },
    shutdown: () => exporter.shutdown(),
    forceFlush: () => exporter.forceFlush(),
  };
}
function start() {
  if (!hosts.has(location.hostname) || Rum.inited) return;
  const params = new URLSearchParams(location.search);
  const area = ['recruiter','outreach'].includes(params.get('workspace') ?? '') ? params.get('workspace')! :
    params.get('id') === 'hr-vision-product--candidate' || location.pathname.includes('candidate') ? 'candidate' :
    params.get('id') === 'hr-vision-product--manager' ? 'employer' : 'public';
  Rum._internalInit({
    applicationName: 'mashadasha-browser', apiKey: undefined,
    url: location.origin + '/telemetry/v1/traces', deploymentEnvironment: 'production', version: __HR_RELEASE__,
    globalAttributes: {'app.area': area},
    resourceAttributes: {'service.version': __HR_RELEASE__, 'deployment.environment': 'production'},
    exporter: {factory: ({url}) => privacyExporter(url)},
    ignoreUrls: [/\/telemetry\//, /^(?!https:\/\/(?:mashadasha\.tech|hr-vision\.158-160-179-53\.sslip\.io)\/api\/hr\/)/],
    instrumentations: {
      ...INSTRUMENTATIONS_ALL_DISABLED,
      fetch: {advancedNetworkCapture: () => false, propagateTraceHeaderCorsUrls: [/\/api\/hr\//]},
      xhr: {advancedNetworkCapture: () => false, propagateTraceHeaderCorsUrls: [/\/api\/hr\//]},
      document: true, longtask: true, webvitals: true,
    },
  });
  // Error messages can contain CV text, email addresses or tokens. Keep type and
  // code locations; all raw messages, rejection values and console are excluded.
  const report = (error: unknown) => {
    const safe = new Error('Browser error (message redacted)');
    safe.name = error instanceof Error && /^[A-Za-z]+Error$/.test(error.name) ? error.name : 'Error';
    safe.stack = error instanceof Error ? safeStack(error.stack, location.origin) : '';
    Rum.recordException(safe, {component: 'error', 'error.message': safe.name, 'error.stack': safe.stack ?? ''});
  };
  window.addEventListener('error', event => report(event.error));
  window.addEventListener('unhandledrejection', event => report(event.reason));
  Recorder.init({
    url: location.origin + '/telemetry/v1/logs', maskAllInputs: true, maskTextSelector: '*',
    blockSelector: 'video,audio,iframe,canvas,img,object,embed,[contenteditable],.ph-no-capture,[data-private],[data-observability-block]',
    recordCanvas: false, recordCrossOriginIframes: false, inlineImages: false, collectFonts: false,
    checkoutEveryNms: 120000,
    sampling: {mousemove: 100, scroll: 250, input: 'last'},
    plugins: [{name: 'mashadasha-privacy', options: {}, eventProcessor: event => sanitizeReplay(event, location.origin)}],
  });
  Rum.addAction('browser.navigation', {'app.area': area});
}
// An unavailable SDK must not prevent the product from rendering.
function safelyStart() { try { start(); } catch { /* optional telemetry must not affect the product */ } }
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', safelyStart, {once: true}); else safelyStart();
