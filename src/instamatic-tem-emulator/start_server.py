import datetime
import logging
import socket
import threading
import time
import traceback
from argparse import ArgumentParser
from dataclasses import dataclass, field
from functools import partial
from multiprocessing.shared_memory import SharedMemory
from queue import Empty, Queue
from tqdm import tqdm
from typing import Any

import numpy as np
from instamatic import config
from instamatic.microscope.interface.simu_microscope import SimuMicroscope
from instamatic.server.serializer import dumper, loader

from simulation.camera import CameraEmulator


stop_program_event = threading.Event()

TEM_PORT = config.settings.tem_server_port
CAM_PORT = config.settings.cam_server_port
BUFFER_SIZE = 1024
NAME = 'emulator'
TIMEOUT = 0.5

date = datetime.datetime.now().strftime('%Y-%m-%d')
logfile = config.locations['logs'] / f'instamatic_TEM_emulator_{date}.log'
logging_fmt = '%(asctime)s %(name)-4s: %(levelname)-8s %(message)s'
logging.basicConfig(level=logging.INFO, filename=logfile, format=logging_fmt)


class SharedImageProxy:
    memory = None

    @classmethod
    def initialize(cls, image_size: int) -> None:
        cls.release()
        try:
            cls.memory = SharedMemory(name=NAME, create=True, size=image_size)
        except FileExistsError:  # if the stale shared memory exists somewhere
            stale = SharedMemory(name=NAME)
            stale.close()
            try:
                stale.unlink()
            except FileNotFoundError:
                pass
            cls.memory = SharedMemory(name=NAME, create=True, size=image_size)
        logging.info(f'New SharedMemory(name="{NAME}", size={image_size}) created')

    @classmethod
    def push(cls, image) -> None:
        image_size = image.nbytes
        if cls.memory is None or cls.memory.size != image_size:
            cls.initialize(image_size=image_size)
        b = np.ndarray(image.shape, dtype=image.dtype, buffer=cls.memory.buf)
        b[:] = image[:]

    @classmethod
    def release(cls) -> None:
        if cls.memory is None:
            return
        cls.memory.close()
        try:
            cls.memory.unlink()
        except FileNotFoundError:
            pass
        cls.memory = None


EmulatedDeviceImplementation = Any  # CameraBase/MicroscopeBase subclass instance


@dataclass
class EmulatedDeviceKind:
    """Declares devices that can be handled by the EmulatedDeviceServer"""
    name: str  # a human-readable noun that describes the device kind
    cls: EmulatedDeviceImplementation
    log: logging.Logger = field(default_factory=logging.getLogger)
    queue: Queue = field(default_factory=partial(Queue, maxsize=100))
    response_cache: list[tuple[int, Any]] = field(default_factory=list)
    is_working: threading.Condition = field(default_factory=threading.Condition)


class EmulatedDeviceServer(threading.Thread):
    """Generalised server that receives commands via connection and passes them
    to the underlying `_device`, be it TEM, camera, or anything else.
    """

    device_implementation_run_kwargs = {}

    def __init__(self, device_kind: EmulatedDeviceKind, **device_kwargs) -> None:
        """Initialize appropriate device kind and connect to the device"""
        super(EmulatedDeviceServer, self).__init__()
        self.device: EmulatedDeviceImplementation = None
        self._device_init_kwargs = device_kwargs or {}
        self._device_kind: EmulatedDeviceKind = device_kind
        self.verbose = False

    def run(self) -> None:
        """Continuously communicate with the underlying `_device`"""
        self.device = self._device_kind.cls(**self._device_init_kwargs)
        thread_desc = f'{self.device.name} {self._device_kind.name} server thread'
        self._device_kind.log.info('Started ' + thread_desc)
        while True:
            try:
                cmd = self._device_kind.queue.get(timeout=TIMEOUT)
            except Empty:
                if stop_program_event.is_set():
                    break
                continue
            with self._device_kind.is_working:
                func_name = cmd.get('func_name', cmd.get('attr_name'))
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
                self._device_kind.log.debug("%s  %s: %s" % (status, func_name, ret))
        self._device_kind.log.info('Terminating ' + thread_desc)

    def evaluate(self, func_name: str, args: list, kwargs: dict) -> Any:
        """Eval and call `self._device.func_name` with `args` and `kwargs`."""
        self._device_kind.log.debug(f'eval {func_name}, {args}, {kwargs}')
        f = getattr(self.device, func_name)
        try:
            ret = f(*args, **kwargs)
        except TypeError:  # TypeError: 'attribute class' object is not callable
            ret = f
        if func_name in {'get_image', 'get_movie'}:
            SharedImageProxy.push(image=ret)
            ret = {'name': NAME, 'shape': ret.shape, 'dtype': str(ret.dtype)}
        return ret


def handle(connection: socket.socket, device_kind: EmulatedDeviceKind) -> None:
    """Pass commands via connection on the queue to server, register response"""
    with connection:
        while True:
            if stop_program_event.is_set():
                break

            if not (data := connection.recv(BUFFER_SIZE)):
                break

            data = loader(data)

            if data == 'exit' or data == 'kill':  # can't use "in", dict is unhashable
                break

            with device_kind.is_working:
                device_kind.queue.put(data)
                device_kind.is_working.wait()
                response = device_kind.response_cache.pop()
                connection.send(dumper(response))


def listen_on(port: int, device_kind: EmulatedDeviceKind) -> None:
    """Listen on a given port and handle incoming instructions"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as device_client:
        device_client.bind(("localhost", port))
        device_client.settimeout(TIMEOUT)
        device_client.listen()
        device_kind.log.info(f'Started {device_kind.name} listener thread')
        while True:
            if stop_program_event.is_set():
                break
            try:
                connection, _ = device_client.accept()
                handle(connection, device_kind)
            except socket.timeout:
                pass
            except Exception as e:
                device_kind.log.exception('Exception when handling connection: %s', e)
        device_kind.log.info(f'Terminating {device_kind.name} listener thread')


def main() -> None:
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
    - `kwargs`: (Optional) Dictionary of keyword arguments for the function (dict)

    The response is returned as a serialized object.
    """

    parser = ArgumentParser(description=main.__doc__)
    parser.add_argument(
        '-v',
        '--verbose',
        action='store_true',
        dest='verbose',
        help='Log DEBUG messages in addition to standard INFO-level messages',
        default=0,
    )
    options = parser.parse_args()
    if options.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    logging.info(f'{NAME.title()} starting')

    tem = EmulatedDeviceKind('microscope', SimuMicroscope, logging.getLogger('tem'))
    cam = EmulatedDeviceKind('camera', CameraEmulator, logging.getLogger('cam'))

    tem_server = EmulatedDeviceServer(device_kind=tem)
    tem_server.start()

    for _ in tqdm(range(100), desc='Waiting for TEM device', leave=False):
        if getattr(tem_server, 'device') is not None:  # wait until TEM initialized
            break
        time.sleep(0.05)
    else:  # extremely unlikely, only raises if simulated TEM can't start in 5 s
        raise RuntimeError('Could not start TEM device on server in 5 seconds')

    cam_server = EmulatedDeviceServer(device_kind=cam, tem=tem_server.device)
    cam_server.start()

    tem_listener = threading.Thread(target=listen_on, args=(TEM_PORT, tem))
    tem_listener.start()

    cam_listener = threading.Thread(target=listen_on, args=(CAM_PORT, cam))
    cam_listener.start()

    try:
        while not stop_program_event.is_set(): time.sleep(TIMEOUT)
    except KeyboardInterrupt:
        logging.info("Received KeyboardInterrupt, shutting down...")
    finally:
        stop_program_event.set()
        SharedImageProxy.release()
        tem_server.join()
        cam_server.join()
        tem_listener.join()
        cam_listener.join()
        logging.info(f'{NAME.title()} shutting down')
        logging.shutdown()


if __name__ == '__main__':
    main()
