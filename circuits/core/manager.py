# Package:  manager
# Date:     11th April 2010
# Author:   James Mills, prologic at shortcircuit dot net dot au

"""Manager

This module definse the Manager class subclasses by component.BaseComponent
"""

from time import sleep
from warnings import warn
from inspect import getargspec
from traceback import format_tb
from sys import exc_info as _exc_info
from collections import deque, defaultdict
from signal import signal, SIGINT, SIGTERM
from threading import current_thread, Thread
from multiprocessing import current_process, Process

try:
    from greenlet import getcurrent as getcurrent_greenlet, greenlet
    GREENLET = True
except ImportError:
    GREENLET = False

from .values import Value
from .events import Event, Started, Stopped, Signal
from .events import Error, Success, Failure, Filter, Start, End

TIMEOUT = 0.01  # 10ms timeout when no tick functions to process


def walk(x, d=0, v=None):
    if not v:
        v = set()
    yield x
    for c in x.components.copy():
        if c not in v:
            v.add(c)
            for r in walk(c, v):
                yield r


class Manager(object):
    """Manager

    This is the base Manager of the BaseComponent which manages an Event Queue,
    a set of Event Handlers, Channels, Tick Functions, Registered and Hidden
    Components, a Task and the Running State.

    :ivar manager: The Manager of this Component or Manager
    """

    def __init__(self, *args, **kwargs):
        "initializes x; see x.__class__.__doc__ for signature"

        self._ticks = set()
        self._tasks = set()
        self._cache = dict()
        self._queue = deque()

        self._task = None
        self._proc = None
        self._bridge = None
        self._thread = None
        self._running = False

        self.handlers = set()
        self.components = set()
        self.root = self.manager = self

    def __repr__(self):
        "x.__repr__() <==> repr(x)"

        name = self.__class__.__name__

        if hasattr(self, "channel") and self.channel is not None:
            channel = "/%s" % self.channel
        else:
            channel = ""

        q = len(self._queue)
        state = self.state

        pid = current_process().pid

        if pid:
            id = "%s:%s" % (pid, current_thread().getName())
        else:
            id = current_thread().getName()

        format = "<%s%s %s (queued=%d) [%s]>"
        return format % (name, channel, id, q, state)

    def __contains__(self, y):
        """x.__contains__(y) <==> y in x

        Return True if the Component y is registered.
        """

        components = self.components.copy()
        return y in components or y in [c.__class__ for c in components]

    def __len__(self):
        """x.__len__() <==> len(x)

        Returns the number of events in the Event Queue.
        """

        return len(self._queue)

    def __add__(self, y):
        """x.__add__(y) <==> x+y

        (Optional) Convenience operator to register y with x
        Equivalent to: y.register(x)

        @return: x
        @rtype Component or Manager
        """

        y.register(self)
        return self

    def __iadd__(self, y):
        """x.__iadd__(y) <==> x += y

        (Optional) Convenience operator to register y with x
        Equivalent to: y.register(x)

        @return: x
        @rtype Component or Manager
        """

        y.register(self)
        return self

    def __sub__(self, y):
        """x.__sub__(y) <==> x-y

        (Optional) Convenience operator to unregister y from x.manager
        Equivalent to: y.unregister()

        @return: x
        @rtype Component or Manager
        """

        if y.manager is not y:
            y.unregister()
        return self

    def __isub__(self, y):
        """x.__sub__(y) <==> x -= y

        (Optional) Convenience operator to unregister y from x
        Equivalent to: y.unregister()

        @return: x
        @rtype Component or Manager
        """

        if y.manager is not y:
            y.unregister()
        return self

    @property
    def name(self):
        """Return the name of this Component/Manager"""

        return self.__class__.__name__

    @property
    def running(self):
        """Return the running state of this Component/Manager"""

        return self._running

    @property
    def state(self):
        """Return the current state of this Component/Manager

        The state can be one of:
         - [R]unning
         - [D]ead
         - [S]topped
        """

        if self.running or (self._task and self._task.isAlive()):
            return "R"
        elif self._task and not self._task.isAlive():
            return "D"
        else:
            return "S"

    def _fire(self, event, channel):
        self._queue.append((event, channel))

    def fireEvent(self, event, *channel, **kwargs):
        """Fire/Push a new Event into the system (queue)

        This will push the given Event, Channel and Target onto the
        Event Queue for later processing.

        If this Component's Manager is itself, enqueue on this Component's
        Event Queue, otherwise enqueue on this Component's Manager.

        :param event: The Event Object
        :type  event: Event

        :param channel: The Channel this Event is bound for
        :type  channel: str
        """

        event.channel = channel
        event.value = Value(event, self)

        if event.start is not None:
            self.fire(Start(event), event.start)

        self.root._fire(event, channel)

        return event.value

    fire = fireEvent

    def push(self, *args, **kwargs):
        """Deprecated in 1.6

        .. deprecated:: 1.6
           Use :py:meth:`fire` instead.
        """

        warn(DeprecationWarning("Use .fire(...) instead"))

        return self.fire(*args, **kwargs)

    def registerTask(self, g):
        self._tasks.add(g)

    def unregisterTask(self, g):
        self._tasks.remove(g)

    def waitEvent(self, cls, limit=None):
        if self._thread and self._thread != current_thread():
            raise Exception("Cannot use .waitEvent from other thread")
        if self._proc and self._proc != current_process():
            raise Exception("Cannot use .waitEvent from other process")

        g = getcurrent_greenlet()

        is_instance = isinstance(cls, Event)
        i = 0
        e = None
        caller = self._task

        self.registerTask(g)
        try:
            while e != cls if is_instance else e.__class__ != cls:
                if limit and i == limit or self._task is None:
                    return
                e, caller = caller.switch()
                i += 1
        finally:
            self.unregisterTask(g)

        return e

    wait = waitEvent

    def callEvent(self, event, *channel):
        self.fire(event, channel)
        e = self.waitEvent(event)
        return e.value

    call = callEvent

    def _flush(self):
        q = self._queue
        self._queue = deque()

        if GREENLET:
            for event, channel in q:
                dispatcher = greenlet(self._dispatcher)
                dispatcher.switch(event, channel)
        else:
            for event, channel in q:
                self._dispatcher(event, channel)

    def flushEvents(self):
        """Flush all Events in the Event Queue"""

        self.root._flush()

    flush = flushEvents

    def _find_handlers(self, channel):
        if channel in self._cache:
            return self.cache[channel]

        handlers = []

        parts = channel.split(".")

        if len(parts) == 1:
            for handler in self.handlers:
                if channel in handler.channels:
                    handlers.append(handler)
        elif len(parts) == 2:
            if parts[0] == "*":
                components = list(walk(self))
            else:
                components = [x for x in walk(self) if x.channel == parts[0]]

            for component in components:
                for handler in component.handlers:
                    if parts[1] in handler.channels:
                        handlers.append(handler)
        else:
            raise SyntaxError("Invalid channel %r" % channel)

        self._cache[channel] = handlers

        return handlers

    def _dispatcher(self, event, channel):
        eargs = event.args
        ekwargs = event.kwargs

        retval = None
        handler = None

        handlers = self._find_handlers(channel)

        for handler in handlers[:]:
            error = None
            event.handler = handler

            try:
                if handler.event:
                    retval = handler(event, *eargs, **ekwargs)
                else:
                    retval = handler(*eargs, **ekwargs)
                event.value.value = retval
            except (KeyboardInterrupt, SystemExit):
                raise
            except:
                etype, evalue, etraceback = _exc_info()
                traceback = format_tb(etraceback)
                error = (etype, evalue, traceback)

                event.value.errors = True

                event.value.value = error

                if event.failure is not None:
                    self.fire(Failure(event, handler, error), *event.failure)
                else:
                    self.fire(Error(etype, evalue, traceback, handler))

            if retval and handler.filter:
                if event.filter is not None:
                    self.fire(Filter(event, handler, retval), *event.filter)
                return

            if error is None and event.success is not None:
                self.fire(Success(event, handler, retval), *event.success)

        if event.end is not None:
            self.fire(End(event, handler, retval), *event.end)

        if GREENLET:
            for task in self._tasks.copy():
                task.switch(event, getcurrent_greenlet())

    def _signalHandler(self, signal, stack):
        self.fire(Signal(signal, stack))
        if signal in [SIGINT, SIGTERM]:
            self.stop()

    def start(self, log=True, link=None, process=False):
        group = None
        target = self.run
        name = self.__class__.__name__
        mode = "P" if process else "T"
        args = ()
        kwargs = {'log': log, '__mode': mode}

        if process:
            if link is not None and isinstance(link, Manager):
                from circuits.net.sockets import Pipe
                from circuits.core.bridge import Bridge
                from circuits.core.utils import findroot
                root = findroot(link)
                parent, child = Pipe()
                self._bridge = Bridge(root, socket=parent)
                self._bridge.start()
                kwargs['__socket'] = child

            self._proc = Process(group, target, name, args, kwargs)
            self._proc.daemon = True
            self.tick()
            self._proc.start()
            return

        self._thread = Thread(group, target, name, args, kwargs)
        self._thread.setDaemon(True)
        self._thread.start()

    def stop(self):
        self._running = False
        self.fire(Stopped(self))
        if self._proc and self._proc.is_alive() \
                and not current_process() == self._proc:
            self._proc.terminate()
        if (self._bridge is not None):
            self._bridge = None
        self._process = None
        self._thread = None
        self._task = None
        self.tick()

    def tick(self):
        try:
            [f() for f in self._ticks.copy()]
            if self:
                self.flush()
            else:
                sleep(TIMEOUT)
        except (KeyboardInterrupt, SystemExit):
            raise
        except:
            etype, evalue, etraceback = _exc_info()
            self.fire(Error(etype, evalue, format_tb(etraceback)))

    def run(self, *args, **kwargs):
        log = kwargs.get("log", True)
        __mode = kwargs.get("__mode", None)
        __socket = kwargs.get("__socket", None)


        if GREENLET:
            self._task = greenlet(self._run)
            self._task.switch(log, __mode, __socket)
        else:
            self._run(log, __mode, __socket)

    def _run(self, log, __mode, __socket):
        if current_thread().getName() == "MainThread":
            try:
                signal(SIGINT,  self._signalHandler)
                signal(SIGTERM, self._signalHandler)
            except ValueError:
                pass  # Ignore if we can't install signal handlers

        if __socket is not None:
            from circuits.core.bridge import Bridge
            manager = Manager()
            bridge = Bridge(manager, socket=__socket)
            self.register(manager)
            manager.start()

        self._running = True

        self.fire(Started(self, __mode))

        try:
            while self or self.running:
                self.tick()
        finally:
            if __socket is not None:
                while bridge or manager:
                    self.tick()
                manager.stop()
                bridge.stop()
            self.stop()
