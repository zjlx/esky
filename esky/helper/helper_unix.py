"""

  esky.helper.helper_unix:  platform-specific functionality for esky.helper

"""

import os
import sys
import errno
import base64
import struct
import signal
import subprocess
import tempfile
from functools import wraps

try:
    import cPickle as pickle
except ImportError:
    import pickle


def has_root():
    """Check whether the use current has root access."""
    return (os.geteuid() == 0)


def can_get_root():
    """Check whether the usee may be able to get root access.

    This is currently always True on unix-like platforms, since we have no
    way of peering inside the sudoers file.
    """
    return True


class DuplexPipe(object):
    """A two-way pipe for communication with a subprocess.

    On unix this is implemented via a pair of fifos.
    """

    def __init__(self,data=None):
        self.rfd = None
        self.wfd = None
        if data is None:
            self.tdir = tempfile.mkdtemp()
            self.rnm = os.path.join(self.tdir,"pipeout")
            self.wnm = os.path.join(self.tdir,"pipein")
            os.mkfifo(self.rnm,0600)
            os.mkfifo(self.wnm,0600)
        else:
            self.tdir,self.rnm,self.wnm = data

    def connect(self):
        return DuplexPipe((self.tdir,self.wnm,self.rnm))

    def read(self,size):
        if self.rfd is None:
            self.rfd = os.open(self.rnm,os.O_RDONLY)
        data = os.read(self.rfd,size)
        return data

    def write(self,data):
        if self.wfd is None:
            self.wfd = os.open(self.wnm,os.O_WRONLY)
        return os.write(self.wfd,data)

    def close(self):
        os.close(self.rfd)
        os.close(self.wfd)
        os.unlink(self.wnm)
        if not os.listdir(self.tdir):
            os.rmdir(self.tdir)


class SubprocPipe(object):
    """Pipe through which to communicate objects with a subprocess.

    This class provides simple inter-process communication of python objects,
    by pickling them and writing them to a pipe in a length-delimited format.
    """

    def __init__(self,proc,pipe):
        self.proc = proc
        self.pipe = pipe

    def read(self):
        """Read the next object from the pipe."""
        sz = self.pipe.read(4)
        if len(sz) < 4:
            raise EOFError
        sz = struct.unpack("I",sz)[0]
        data = self.pipe.read(sz)
        if len(data) < sz:
            raise EOFError
        return pickle.loads(data)

    def write(self,obj):
        """Write the given object to the pipe."""
        data = pickle.dumps(obj,pickle.HIGHEST_PROTOCOL)
        self.pipe.write(struct.pack("I",len(data)))
        self.pipe.write(data)

    def close(self):
        """Close the pipe."""
        self.pipe.close()

    def terminate(self):
        """Terminate the attached subprocess, if any."""
        if self.proc is not None:
            if hasattr(self.proc,"terminate"):
                self.proc.terminate()
            else:
                os.kill(self.proc.pid,signal.SIGTERM)


def find_helper():
    """Find the exe for the helper app."""
    if getattr(sys,"frozen",False):
        return [os.path.join(os.path.dirname(sys.executable),
                            "esky-update-helper")]
    return [sys.executable,"-m","esky.helper.__main__"]


def find_exe(name,*args):
    for dir in os.environ.get("PATH","/bin:/usr/bin").split(":"):
        path = os.path.join(dir,name)
        if os.path.exists(path):
            return [path] + list(args)
    return None


def spawn_helper(esky,as_root=False):
    """Spawn the helper app, returning a SubprocPipe connected to it."""
    rnul = open(os.devnull,"r")
    wnul = open(os.devnull,"w")
    p_pipe = DuplexPipe()
    c_pipe = p_pipe.connect()
    data = pickle.dumps(c_pipe,pickle.HIGHEST_PROTOCOL)
    exe = find_helper() + [base64.b64encode(data)]
    exe.append(base64.b64encode(pickle.dumps(esky)))
    #  Look for a variety of sudo-like programs
    if as_root:
        sudo = None
        display_name = "%s updater" % (esky.name,)
        if "DISPLAY" in os.environ:
            sudo = find_exe("gksudo","-k","-D",display_name,"--")
            if sudo is None:
                sudo = find_exe("kdesudo")
        if sudo is None:
            sudo = find_exe("sudo")
        if sudo is not None:
            exe = sudo + exe
    #  Spawn the subprocess
    kwds = dict(stdin=rnul)
    p = subprocess.Popen(exe,**kwds)
    pipe = SubprocPipe(p,p_pipe)
    return pipe

