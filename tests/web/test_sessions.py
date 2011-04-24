#!/usr/bin/env python

from circuits.web import Controller, Sessions

from .helpers import build_opener, HTTPCookieProcessor
from .helpers import CookieJar


class Root(Controller):

    def index(self, name=None):
        if name:
            self.session["name"] = name
        else:
            name = self.session.get("name", "World!")

        return "Hello %s" % name

def test(webapp):
    Sessions().register(webapp)

    cj = CookieJar()
    opener = build_opener(HTTPCookieProcessor(cj))

    f = opener.open(webapp.server.base)
    s = f.read() 
    assert s == b"Hello World!"

    f = opener.open(webapp.server.base + "/test")
    s = f.read()
    assert s == b"Hello test"

    f = opener.open(webapp.server.base)
    s = f.read()
    assert s == b"Hello test"
