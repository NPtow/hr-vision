"""HH browser automation hosted by the existing hh-daily Playwright owner.

This package never launches a browser or calls the HH HTTP API.
"""
from .adapter import BrowserError, HHBrowserAdapter, SelectorProfile
from .bridge import BrowserBridgeClient, WorkerBridge

__all__ = ['BrowserError', 'HHBrowserAdapter', 'SelectorProfile', 'BrowserBridgeClient', 'WorkerBridge']
