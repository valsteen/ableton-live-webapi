from __future__ import with_statement
import logging

import sys
import threading
import zmq
import simplejson
import time


# Import Live libraries
import Live
import types
from _Framework.ControlSurface import ControlSurface


PUSH_ADDRESS = "tcp://127.0.0.1:5554"
PULL_ADDRESS = "tcp://127.0.0.1:5553"
PUB_ADDRESS = "tcp://127.0.0.1:5552"


class WebAPI(ControlSurface):
    @property
    def application(self):
        if not hasattr(self, "_application"):
            self._application = Live.Application.get_application()
        return self._application

    @property
    def document(self):
        if not hasattr(self, "_document"):
            self._document = self.application.get_document()
        return self._document


    def get_object(self, path):
        obj = self
        parts = path.split(".")
        attr = parts.pop()

        for part in parts:
            try:
                index = int(part)
                obj = obj[index]
            except ValueError:
                obj = getattr(obj, part)

        return obj, attr

    def create_listener(self, obj, attr):
        def listener():
            try:
                if not hash(obj) in self.updated_queue:
                    self.updated_queue[hash(obj)] = {}
                self.updated_queue[hash(obj)][attr] = (obj, attr)
            except Exception, e:
                self.log_message(repr(e))

        return listener

    def update_display(self):
        ControlSurface.update_display(self)

        for _id, attrs in self.updated_queue.items():
            for _, (obj, attr) in attrs.items():
                try:
                    value = getattr(obj, attr)
                except:
                    # not all listeners are mapped directly to a property. just callback without any value
                    value = None
                self.pub_socket.send(simplejson.dumps({'id': _id, 'attribute': attr, 'value': value}))
        self.updated_queue = {}

    rconsole_started = False

    def receive_midi(self, midi_bytes):
        try:
            if len(midi_bytes) == 2:
                while True:
                    try:
                        req = self.pull_socket.recv()
                        self.log_message(req)
                    except zmq.ZMQError:
                        break
                    self.respond(simplejson.loads(req), self.push_socket.send)
        except Exception, e:
            self.log_message(repr(e))

    def defer_set(self, obj, attr, value):
        # console helper

        event = threading.Event()
        event.clear()

        result_holder = {}

        def deferred():
            try:
                setattr(obj, attr, value)
                result_holder['error'] = None
            except Exception, e:
                result_holder['error'] = e
            event.set()

        self.schedule_message(1, deferred)
        event.wait()
        return result_holder['error']

    def defer_call(self, method, *parameters):
        # console helper

        event = threading.Event()
        event.clear()

        result_holder = {}

        def deferred():
            try:
                method(*parameters)
                result_holder['error'] = None
            except Exception, e:
                result_holder['error'] = e
            event.set()

        self.schedule_message(1, deferred)
        event.wait()
        return result_holder['error']

    def respond(self, req, cb):
        try:
            # Live API doesn't like lists. Overriding JSON Decoder isn't useful: overriding array parsing isn't possible
            def convert_objects(arg):
                if isinstance(arg, dict):
                    if arg.keys() == ['id']:
                        arg = self.references.get(arg['id'])
                    else:
                        for key in arg.keys():
                            arg[key] = convert_objects(arg[key])
                    return arg
                if isinstance(arg, list):
                    return tuple(convert_objects(item) for item in arg)
                return arg

            result = self.request(convert_objects(req))
            cb(simplejson.dumps(result))
        except Exception, e:
            self.log_message(repr(e))
            cb(simplejson.dumps({'error': repr(e), 'messageId': req["messageId"]}))

    def request(self, req):
        try:
            if req["method"] == "RCONSOLE":
                if not self.rconsole_started:
                    def defer_start():
                        self.logger.debug("Spawning rconsole")
                        from rfoo.utils import rconsole

                        rconsole.spawn_server()
                        self.rconsole_started = True
                        self.logger.debug("rconsole started")

                    self.schedule_message(1, defer_start)
                    result = "ok"
                else:
                    result = "Already started"
            elif req["method"] == "BATCH":
                result = []
                for command in req["commands"]:
                    result.append(self.request(command))
            else:
                if "id" in req:
                    attr = req.get("attribute")
                    obj = self.references[req["id"]]
                else:
                    obj, attr = self.get_object(req["path"])

                if req["method"] == "CALL":
                    result = getattr(obj, attr)(*req.get('parameters', []))
                elif req["method"] == "GET":
                    try:
                        result = getattr(obj, attr)
                    except:
                        try:
                            result = obj[int(attr)]
                        except ValueError:
                            raise Exception("Invalid '%s' attribute for %s" % (attr, obj.__class__.__name__))
                elif req["method"] == "SET":
                    setattr(obj, attr, req.get("value"))
                    result = "ok"
                elif req["method"] == "LIST":
                    try:
                        index = int(attr)
                        objs = obj[index]
                    except ValueError:
                        objs = getattr(obj, attr)

                    result = []

                    for obj in objs:
                        item = dict()
                        item['id'] = hash(obj)
                        item['type'] = obj.__class__.__name__
                        self.references[hash(obj)] = obj
                        for attr in req["attributes"]:
                            item[attr] = getattr(obj, attr)
                        result.append(item)
                elif req["method"] == "DIR":
                    try:
                        index = int(attr)
                        obj = obj[index]
                    except ValueError:
                        obj = getattr(obj, attr)
                    result = dir(obj)
                elif req["method"] == "LISTEN":
                    if not hash(obj) in self.listeners:
                        self.listeners[hash(obj)] = {}

                    if not attr in self.listeners[hash(obj)]:
                        listener = self.create_listener(obj, attr)
                        getattr(obj, "add_%s_listener" % attr)(listener)
                        self.listeners[hash(obj)][attr] = listener
                        result = "ok"
                    else:
                        result = "already listening"
                elif req["method"] == "UNLISTEN":
                    if hash(obj) in self.listeners and attr in self.listeners[hash(obj)]:
                        getattr(obj, "remove_%s_listener" % attr)(self.listeners[hash(obj)][attr])
                        del self.listeners[hash(obj)][attr]
                        result = "ok"
                    else:
                        raise Exception("Not listening")
                else:
                    result = "not implemented"

            if isinstance(result, (
                    types.BooleanType, types.ListType, types.DictType, types.TupleType, types.UnicodeType,
                    types.StringType,
                    types.NoneType, types.LongType, types.IntType, types.FloatType)):
                return {'result': result, 'messageId': req.get("messageId")}
            else:
                # keep a reference of the returned object. We don't expect an object inside a List/Tuple/Dict

                # We want to keep a single reference to any object, and sometimes Live gives the same instance,
                # sometimes not, but == between two references works. We have no other choice than scanning all
                # references, fortunately this is used only at initialization

                if hash(result) in self.references and self.references[hash(result)] == result:
                    return {'result': {'id': hash(result), 'type': result.__class__.__name__}, 'messageId': req.get("messageId") }

                for _id, ref in self.references.items():
                    if ref == result:
                        return {'result': {'id': _id, 'type': result.__class__.__name__}, 'messageId': req.get("messageId")}

                self.references[hash(result)] = result
                return {'result': {'id': hash(result), 'type': result.__class__.__name__}, 'messageId': req.get("messageId")}
        except Exception, e:
            self.log_message(repr(e))
            return {'error': repr(e), 'messageId': req.get("messageId")}


    def pause(self):
        # helper for rconsole. This blocks ableton inside python on purpose, otherwise the console thread
        # is super slow ( python interpreter is only active when needed )

        def lock():
            if self.console_lock.isSet():
                return
            # this trick works with Event and not with just time.sleep, because in python2 Event.wait is a loop+sleep, which keeps the interpreter busy
            self.console_lock.wait(0.1)
            self.schedule_message(3, lock)

        self.console_lock.clear()
        self.schedule_message(3, lock)

    def unpause(self):
        self.console_lock.set()

    def __init__(self, c_instance):
        logfile = logging.FileHandler('/tmp/abletonwebapi.log')
        logfile.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        logfile.setFormatter(formatter)
        self.logger = logging.getLogger("ableton")
        self.logger.setLevel(logging.DEBUG)
        self.logger.addHandler(logfile)
        self.logger.debug("WebAPI connected")

        self.listeners = {}

        self.console_lock = threading.Event()
        self.send_queue_lock = threading.Lock()
        self.send_queue = {}
        self.updated_queue = {}
        self.references = {}

        try:
            self.context = zmq.Context.instance()

            self.push_socket = self.context.socket(zmq.PUSH)
            self.push_socket.setsockopt(zmq.LINGER, 10)
            self.push_socket.bind(PUSH_ADDRESS)

            self.pull_socket = self.context.socket(zmq.PULL)
            self.pull_socket.setsockopt(zmq.LINGER, 10)
            self.pull_socket.setsockopt(zmq.RCVTIMEO, 5)
            self.pull_socket.bind(PULL_ADDRESS)

            self.pub_socket = self.context.socket(zmq.PUB)
            self.pub_socket.setsockopt(zmq.LINGER, 10)
            self.pub_socket.bind(PUB_ADDRESS)

        except Exception, e:
            self.log_message(e)

        ControlSurface.__init__(self, c_instance)

    def log_message(self, *message):
        self.logger.exception(str(message))

    def disconnect(self):
        self.console_lock.set()
        self.push_socket.close()
        self.pull_socket.close()
        self.pub_socket.close()
        self.context.term()
        ControlSurface.disconnect(self)
