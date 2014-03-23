#!/usr/bin/env python

from circuits import handler, BaseComponent, Debugger


class MyComponent(BaseComponent):

    def __init__(self):
        super(MyComponent, self).__init__()

        Debugger().register(self)

    @handler("started", channel="*")
    def system_started(self, component):
        print "Start event detected"

MyComponent().run()
