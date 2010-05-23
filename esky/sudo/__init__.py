"""

  esky.sudo:  spawn a root-privileged helper app to process esky updates.

This module provides the infrastructure for spawning a stand-alone "helper app"
to install updates with root privileges.  The class "SudoProxy" provides a
proxy to the methods of an object via a root-preivileged helper process.

Example:

    app.install_version("1.2.3")
    -->   IOError:  permission denied

    sapp = SudoWrapper(app)
    sapp.start()
    -->   prompts for credentials
    sapp.install_version("1.2.3")
    -->   success!


We also provie some handy utility functions:

    * has_root():      check whether current process has root privileges
    * can_get_root():  check whether current process may be able to get root
    


"""

import sys
from functools import wraps

try:
    import cPickle as pickle
except ImportError:
    import pickle


if sys.platform == "win32":
    from esky.sudo.sudo_win32 import spawn_sudo, has_root, can_get_root,\
                                     run_startup_hooks
else:
    from esky.sudo.sudo_unix import spawn_sudo, has_root, can_get_root,\
                                     run_startup_hooks


class SudoProxy(object):
    """Object method proxy with root privileges."""

    def __init__(self,target):
        self.name = target.name
        self.target = target
        self.pipe = None

    def start(self):
        self.pipe = spawn_sudo(self)
        if self.pipe.read() != "READY":
            self.close()
            raise RuntimeError("failed to spawn helper app")

    def close(self):
        self.pipe.write("close")
        self.pipe.read()
        self.pipe.close()

    def run(self,pipe):
        self.target.sudo_proxy = None
        pipe.write("READY")
        try:
            while True:
                try:
                    methname = pipe.read()
                    if methname == "close":
                        pipe.write("CLOSING")
                        break
                    else:
                        argtypes = _get_sudo_argtypes(self.target,methname)
                        if argtypes is None:
                            msg = "attribute '%s' not allowed from sudo"
                            raise AttributeError(msg % (attr,))
                        method = getattr(self.target,methname)
                        args = [t(pipe.read()) for t in argtypes]
                        try:
                            res = method(*args)
                        except Exception, e:
                            pipe.write(pickle.dumps((False,e)))
                        else:
                            pipe.write(pickle.dumps((True,res)))
                except EOFError:
                    break
        finally:
            pipe.close()

    def __getattr__(self,attr):
        if attr.startswith("_"):
            raise AttributeError(attr)
        target = self.__dict__["target"]
        if _get_sudo_argtypes(target,attr) is None:
            msg = "attribute '%s' not allowed from sudo" % (attr,)
            raise AttributeError(msg)
        method = getattr(target,attr)
        pipe = self.__dict__["pipe"]
        @wraps(method.im_func)
        def wrapper(*args):
            pipe.write(method.im_func.func_name)
            for arg in args:
                pipe.write(str(arg))
            (success,result) = pickle.loads(pipe.read())
            if not success:
                raise result
            return result
        setattr(self,attr,wrapper)
        return wrapper


def allow_from_sudo(*argtypes):
    """Method decorator to allow access to a method via the sudo proxy.

    This decorator wraps an Esky method so that it can be transparently
    called via the esky's sudo proxy when enabled.  It is also used to
    declare type conversions/checks on the arguments given to the
    method.  Example:

        @allow_from_sudo(str)
        def install_version(self,version):
            ...

    """
    def decorator(func):
        @wraps(func)
        def wrapper(self,*args,**kwds):
            if self.sudo_proxy is not None:
                return getattr(self.sudo_proxy,func.func_name)(*args,**kwds)
            return func(self,*args,**kwds)
        wrapper._esky_sudo_argtypes = argtypes
        return wrapper
    return decorator


def _get_sudo_argtypes(obj,methname):
    """Get the argtypes list for the given method.

    This searches the base classes of obj if the given method is not declared
    allowed_from_sudo, so that people don't have to constantly re-apply the
    decorator.
    """
    for base in _get_mro(obj):
        try:
            argtypes = base.__dict__[methname]._esky_sudo_argtypes
        except (KeyError,AttributeError):
            pass
        else:
            return argtypes
    return None


def _get_mro(obj):
    try:
        return obj.__class__.__mro__
    except AttributeError:
        return _get_oldstyle_mro(obj.__class__,set())

def _get_oldstyle_mro(cls,seen):
    yield cls
    seen.add(cls)
    for base in cls.__bases__:
        if base not in seen:
            for ancestor in _get_oldstyle_mro(base,seen):
                yield ancestor

