// Deliberately no SDK and no ready message: leaving never authorizes a rejoin.
if (window.parent !== window) {
  window.parent.postMessage({ source: 'hr-zoom-client', type: 'status', phase: 'left' }, location.origin);
}
