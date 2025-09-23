import dataclasses
import logging
import signal
import socket
import threading
import traceback
from dataclasses import field
from functools import partial
from queue import Queue
from typing import Any, Optional

from serializer import dumper, loader

from instamatic.microscope.interface.simu_microscope import SimuMicroscope


stop_program_event = threading.Event()

HOST = 'localhost'
PORT = 8000
BUFFER_SIZE = 1024


logging.basicConfig(level=logging.INFO)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
console.setFormatter(logging.Formatter('%(name)-4s: %(levelname)-8s %(message)s'))
logging.getLogger().addHandler(console)


EmulatedDeviceImplementation = Any


@dataclasses.dataclass
class EmulatedDeviceKind:
    """Declares devices that can be handled by the EmulatedDeviceServer"""
    name: str  # a human-readable noun that describes the device kind
    cls: EmulatedDeviceImplementation
    log: logging.Logger = field(default_factory=logging.getLogger)
    queue: Queue = field(default_factory=partial(Queue, maxsize=100))
    response_cache: list = field(default_factory=list)
    is_working: threading.Condition = field(default_factory=threading.Condition)


class EmulatedDeviceServer(threading.Thread):
    """Generalised server that receives commands via connection and passes them
    to the underlying `_device`, be it TEM, camera, or anything else.
    """

    device_implementation_run_kwargs = {}

    def __init__(self, device_kind: EmulatedDeviceKind) -> None:
        """Initialize appropriate device kind and connect to the device"""
        super(EmulatedDeviceServer, self).__init__()
        self.device: EmulatedDeviceImplementation = None
        self._device_init_kwargs = {}
        self._device_kind: EmulatedDeviceKind = device_kind
        self.verbose = False

    def run(self):
        """Continuously communicate with the underlying `_device`"""
        self.device = self._device_kind.cls(**self._device_init_kwargs)
        self._device_kind.log.info(f'Initialized connection to {self.device.name}')
        while True:
            cmd = self._device_kind.queue.get()
            with self._device_kind.is_working:
                func_name = cmd['func_name']
                args = cmd.get('args', ())
                kwargs = cmd.get('kwargs', {})

                try:
                    ret = self.evaluate(func_name, args, kwargs)
                    status = 200
                except Exception as e:
                    traceback.print_exc()
                    self._device_kind.log.exception(e)
                    ret = (e.__class__.__name__, e.args)
                    status = 500

                self._device_kind.response_cache.append((status, ret))
                self._device_kind.is_working.notify()
                self._device_kind.log.info("%s  %s: %s" % (status, func_name, ret))

    def evaluate(self, func_name: str, args: list, kwargs: dict):
        """Eval and call `self._device.func_name` with `args` and `kwargs`."""
        logging.info(func_name, args, kwargs)
        f = getattr(self.device, func_name)
        return f(*args, **kwargs)


class EmulatedCamServer(EmulatedDeviceServer):
    """Specific implementation for a tem-aware camera to simulate images"""

    def __init__(self, device_kind: EmulatedDeviceKind, tem: EmulatedDeviceImplementation) -> None:
        """Additionally include tem instance in cam initialization kwargs"""
        super(EmulatedDeviceServer, self).__init__()
        self.device: EmulatedDeviceImplementation = None
        self._device_init_kwargs = {'tem': tem}
        self._kind: EmulatedDeviceKind = device_kind
        self.verbose = False


def handle(connection: socket.socket, device_kind: EmulatedDeviceKind) -> None:
    """Pass commands via connection on the queue to server, register response"""
    with connection:
        while True:
            if stop_program_event.is_set():
                break

            if not (data := connection.recv(BUFFER_SIZE)):
                break

            data = loader(data)

            if data in {'exit', 'kill'}:
                break

            with device_kind.is_working:
                device_kind.queue.put(data)
                device_kind.is_working.wait()
                response = device_kind.response_cache.pop()
                connection.send(dumper(response))


def listen_on(port: int, kind: EmulatedDeviceKind) -> None:
    """Listen on a given port and handle incoming instructions"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("localhost", port))
        s.listen()
        while True:
            connection, _ = s.accept()
            try:
                handle(connection, kind)
            except Exception as e:
                logging.exception('Exception when handling connection: %s', e)


def handle_keyboard_interrupt(*_) -> None:
    stop_program_event.set()


def main():
    """Initialize emulated devices, open and handle communication for each.

    This program starts up an emulated TEM and camera and opens a socket for
    each of them. Both TEM and camera run in separate threads but the camera
    reads the state of the TEM and simulates an image accordingly. The server
    behaves like an actual TEM/camera pair. The HOST and PORT of two opened
    sockets depend on the settings. The purpose of this emulator is to provide
    a stable, performant, consistent, and accurate image simulation for testing.

    Settings (ports, simulation) are defined in `config/settings.yaml`.

    The data sent over the socket is a serialized dictionary with the following elements:

    - `func_name`: Name of the function to call (str)
    - `args`: (Optional) List of arguments for the function (list)
    - `kwargs`: (Optiona) Dictionary of keyword arguments for the function (dict)

    The response is returned as a serialized object.
    """

    # add parser and parser arguments here and uncomment options when needed
    # parser = argparse.ArgumentParser(description=main.__doc__)
    # options = parser.parse_args()

    tem = EmulatedDeviceKind('microscope', SimuMicroscope, logging.getLogger('tem'))
    cam = EmulatedDeviceKind('camera', ..., logging.getLogger('cam'))

    tem_server = EmulatedDeviceServer(device_kind=tem)
    tem_server.start()

    cam_server = EmulatedCamServer(device_kind=cam, tem=tem_server.device)
    cam_server.start()

    signal.signal(signal.SIGINT, handle_keyboard_interrupt)

    threading.Thread(target=listen_on, args=(5000, tem)).start()
    threading.Thread(target=listen_on, args=(5001, cam)).start()
