# Instamatic TEM emulator
Instamatic is an open-source software used to control an arbitrary TEM via a dedicated microscope server and collect diffraction images from an arbitrary camera via a dedicated Camera server. This modularity is achieved due to its client-server architecture, where each TEM and camera type is handled by its own server class that communicates with Instamatic using a common interface. By default, the program provides two simple "simulated" server classes that can be used for set up. Rather than providing a realistic picture of an experiment, these simulation classes are bare-bones and designed mostly for debugging purposes.

In contrast to built-in "simulated" Instamatic TEM and camera, `instamatic-TEM-emulator` program provides a stand-alone TEM and camera "emulator" that can be used to model real experimental conditions. The emulator is an external server that can be used in a testing, development, or teaching environment. It provides the user with a semi-realistic environment, where functionalities, skills or code can be tested without a worry or need for an expensive physical microscope.

## Installation

The server uses Instamatic internally to handle config files and microscope state alongside some diffraction simulation libraries to generate the reciprocal space image. Since Instamatic is one of the dependencies, `instamatic-TEM-emulator` must be installed in (virtual) environment that includes Instamatic. Depending on your use case, this can be configured in two different ways described below.

### User case

If you are an Instamatic user and have no need to edit Instamatic code directly, you have most likely installed Instamatic using either `pip` or `conda`. In this case:

* Locate and activate the (virtual) environment with Instamatic; we will install emulator here for simplicity.
* `git clone` this repository to your computer;
* Run `pip install -r requirements.txt` to download necessary packages (as well as Instamatic, if absent).

### Developer case

If installed Instamatic directly from git, intend to edit Instamatic used internally by `instamatic-TEM-emulator`, or do not want to affect your Instamatic environment with additional packages, point to existing Instamatic at installation:

* Create and activate a new virtual environment;
* `git clone` this repository to your computer;
* Run `pip install -e /path/to/instamatic` to link existing Instamatic to this environment;
* Run `pip install -r requirements.txt` to install the rest of required packages;

In either case, to run the emulator, navigate to `scr/instamatic-tem-emulator` directory and run `python start_server.py`. The repository also includes a handy `start_server.bat` that can be used as a shortcut to start the program.

## Usage

`instamatic-TEM-emulator` should be run on the same computer as the main instance of Instamatic. Remote connection should be possible and require standard set of Instamatic config files but has not been tested. The emulator uses the same config files as the main program. In order to inform Instamatic to connect to the emulator server, assert `settings.yaml` includes the following:
```yaml
simulate: False
use_tem_server: True
tem_server_host: 'localhost'
use_cam_server: True
cam_server_host: 'localhost'
```

Note that due to peculiarities of instamatic client-server architecture, in camera config also specify `interface: serval` or other interface other than `simulate`. This is used only to establish an interface, and the peculiarity will be patched in the future. Instamatic must also allow for remote streaming cameras, the feature enforced by commenting out both `self.streamable = False` in `instamatic.camera:CamClient` and `as_stream = False` in `instamatic.camera:Camera`. As this change will be pushed to Instamatic soon, this README line will be updated to reflect it.

With these config setting in place, the emulator server can be start up by running `python start_server.py`. This starts up an emulated TEM and camera and opens a socket for each of them. Both emulated TEM and camera run in separate threads, but the camera reads the state of the TEM and simulates an image accordingly. The server behaves like an actual TEM/camera pair. The purpose of this emulator is to provide a stable, performant, consistent, and accurate image simulation for testing.

## Credits

Simulation code was provided by [Viljar Femoen](https://github.com/viljarjf), see Instamatic [#104](https://github.com/instamatic-dev/instamatic/issues/104), [#105](https://github.com/instamatic-dev/instamatic/pull/105), [#140](https://github.com/instamatic-dev/instamatic/pull/140), and [#141](https://github.com/instamatic-dev/instamatic/pull/141). The generalized server architecture was reworked by Daniel Tchoń based on Steffen Schmidt's [instamatic-tecnai-server](https://github.com/instamatic-dev/instamatic-tecnai-server).

For further details on Instamatic, requirements, dependencies, documentation, reference, code maintenance or contribution details, please refer to [the main repository of Instamatic](https://github.com/instamatic-dev/instamatic/).
