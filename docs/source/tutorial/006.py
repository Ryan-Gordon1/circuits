#!/usr/bin/env python

from circuits import Component, Event

class Pound(Component):

    def __init__(self):
        super(Pound, self).__init__()

        self.bob = Bob().register(self)
        self.fred = Fred().register(self)

    def started(self, *args):
        self.fireEvent(Event(), "woof")

class Dog(Component):

    def woof(self):
        print("Woof! I'm %s!" % self.name)

class Bob(Dog):
    """Bob"""

class Fred(Dog):
    """Fred"""

Pound().run()
