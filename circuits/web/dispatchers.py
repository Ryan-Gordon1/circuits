# Module:   dispatcher
# Date:     13th September 2007
# Author:   James Mills, prologic at shortcircuit dot net dot au

"""Dispatchers

This module implements URL dispatchers.
"""

import os
from urlparse import urljoin as _urljoin

from circuits import handler, Component

from core import Controller
from errors import HTTPError
from cgifs import FieldStorage
from tools import expires, serve_file
from utils import parseQueryString, dictform

class Dispatcher(Component):

    channel = "web"

    def __init__(self, docroot=None, defaults=None, **kwargs):
        super(Dispatcher, self).__init__(**kwargs)

        self.docroot = docroot or os.getcwd()
        self.defaults = defaults or ["index.xhtml", "index.html", "index.htm"]

        self.paths = []
        self.cache = {}

    def _parseBody(self, request, response, params):
        body = request.body
        headers = request.headers

        if "Content-Type" not in headers:
            headers["Content-Type"] = ""

        try:
            form = FieldStorage(fp=body,
                headers=headers,
                environ={"REQUEST_METHOD": "POST"},
                keep_blank_values=True)
        except Exception, e:
            if e.__class__.__name__ == 'MaxSizeExceeded':
                # Post data is too big
                return HTTPError(request, response, 413)
            else:
                raise

        if form.file:
            request.body = form.file
        else:
            params.update(dictform(form))

        return True

    def _getChannel(self, request):
        """_getChannel(request) -> channel

        Find and return an appropriate channel for the given request.

        The channel is found by traversing the system's event channels,
        and matching path components to successive channels in the system.

        If a channel cannot be found for a given path, but there is
        a default channel, then this will be used.
        """

        path = request.path
        if path in self.cache:
            return self.cache[path]

        method = request.method.upper()
        request.index = request.path.endswith("/")

        names = [x for x in path.strip("/").split("/") if x]

        if not names:
            for default in ("index", method, "default"):
                k = ("/", default)
                if k in self.channels:
                    self.cache[path] = r = (default, "/", [])
                    return r
            self.cache[path] = r = (None, None, [])
            return r

        i = 0
        matches = [""]
        candidates = []
        while i <= len(names):
            x = "/".join(matches) or "/"
            if x in self.paths:
                candidates.append([i, x])
                if i < len(names):
                    matches.append(names[i])
            else:
                break
            i += 1

        if not candidates:
            self.cache[path] = r = (None, None, [])
            return r

        i, candidate = candidates.pop()

        if i < len(names):
            channels = [names[i], "index", method, "default"]
        else:
            channels = ["index", method, "default"]

        vpath = []
        channel = None
        for channel in channels:
            if (candidate, channel) in self.channels:
                if i < len(names) and channel == names[i]:
                    i += 1
                break

        if channel is not None:
            if i < len(names):
                vpath = [x.replace("%2F", "/") for x in names[i:]]
            else:
                vpath = []

        handler = self.channels[(candidate, channel)][0]

        if vpath and not handler.varargs:
            self.cache[path] = r = (None, None, [])
            return r
        else:
            self.cache[path] = r = (channel, candidate, vpath)
            return r

    @handler("registered", target="*")
    def registered(self, c, m):
        if isinstance(c, Controller) and c not in self.components:
            self.paths.append(c.channel)
            c.unregister()
            self += c

    @handler("unregistered", target="*")
    def unregistered(self, c, m):
        if isinstance(c, Controller) and c in self.components and m == self:
            self.paths.remove(c.channel)

    @handler("request", filter=True)
    def request(self, event, request, response):
        req = event
        path = request.path.strip("/")

        filename = None

        if path:
            filename = os.path.abspath(os.path.join(self.docroot, path))
        else:
            for default in self.defaults:
                filename = os.path.abspath(os.path.join(self.docroot, default))
                if os.path.exists(filename):
                    break

        if filename and os.path.exists(filename):
            expires(request, response, 3600*24*30)
            return serve_file(request, response, filename)

        channel, target, vpath = self._getChannel(request)

        if channel and target:
            req.kwargs = parseQueryString(request.qs)
            v = self._parseBody(request, response, req.kwargs)
            if not v:
                return v # MaxSizeExceeded (return the HTTPError)

            if vpath:
                req.args += tuple(vpath)

            return self.send(req, channel, target, errors=True)

class VirtualHosts(Component):
    """Forward to anotehr Dispatcher based on the Host header.
    
    This can be useful when running multiple sites within one server.
    It allows several domains to point to different parts of a single
    website structure. For example:
     - http://www.domain.example      -> /
     - http://www.domain2.example     -> /domain2
     - http://www.domain2.example:443 -> /secure
    
    @param domains: a dict of {host header value: virtual prefix} pairs.
    @type  domains: dict

    The incoming "Host" request header is looked up in this dict,
    and, if a match is found, the corresponding "virtual prefix"
    value will be prepended to the URL path before passing the
    request onto the next dispatcher.

    Note that you often need separate entries for "example.com"
    and "www.example.com". In addition, "Host" headers may contain
    the port number.
    """

    channel = "web"

    def __init__(self, **domains):
        super(Dispatcher, self).__init__()

        self.domains = domains

    @handler("request", filter=True)
    def request(self, event, request, response):
        req = event
        path = request.path.strip("/")

        header = req.headers.get
        domain = header("X-Forwarded-Host", header("Host", ""))
        prefix = self.domains.get(domain, "")
        if prefix:
            path = _urljoin(prefix, path)
        request.path = path
        
        return self.send(req, "request")
