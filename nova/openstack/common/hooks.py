# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2011 OpenStack LLC.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""
A thin(ish) wrapper around extensions
"""
import collections
import ConfigParser
import functools
import logging

import extensions


log = logging.getLogger(__name__)


_enabled_hooks = collections.defaultdict(list)


def hooks(module, name):
    """
    Helper function to get hooks from extensions including in entry_points
    """
    return extensions.get(group=module, name=name,
                          consume_entry_points=True)


def listener_enabled(module, name, listener_name):
    """
    A hook filter that returns True if the listener has been enabled for the
    hook
    """
    hook_name = '%s:%s' % (module, name)
    if listener_name in _enabled_hooks[hook_name]:
        return True
    return False


def enable_for_hook(module, name, listener_name):
    """
    Enable a hook listener for the hook named <module>:<name>
    """
    hook_name = '%s:%s' % (module, name)
    if listener_name not in _enabled_hooks[hook_name]:
        _enabled_hooks[hook_name].append(listener_name)


def enable_hooks_file(path):
    """
    Enables listeners for hooks from a config-like file.

    For example:

    [hook-openstack.common.hooks:myhook]
    listeners = mymodule:mylistener, mymodule2:myotherlistener

    Will enable both listeners for the hook myhook in the module
    openstack.common.hooks
    """
    parser = ConfigParser.SafeConfigParser()
    parser.read([path])
    for section in parser.sections():
        if not section.startswith('hook-'):
            continue

        hook_name = section.split('-', 1)[-1]
        module, name = hook_name.split(':')

        for listener in parser.get(section, 'listeners').split(','):
            enable_for_hook(module, name, listener.strip())


def hooks_trigger(module, name, filter_func=lambda m, n, l: True,
                  allow_raise=False):
    """
    Return a callable that will call all enabled hooks registered in the
    given module with the given name with any arguments passed.

    Hook callables are loaded once and called everytime the callable is
    invoked. If filter_func is provided, hooks will only be called if
    it returns True when passed the hook_name and listener_name. Both args
    are the doted colon separated name of the hook or listener
    (ex. openstack.common.hooks:myhook or mymodule:mylistener). By default
    all hooks are enabled.

    Exceptions in hooks are caught and logged unless allow_raise is set
    to True, in which case the exception will be logged and re-raised

    """
    _listeners = []

    for listener in hooks(module, name):
        listener_name = '%s:%s' % (listener.module_name,
                                   '.'.join(listener.attrs))
        if not filter_func(module, name, listener_name):
            continue
        try:
            listener.call = listener.load()
            _listeners.append(listener)
        except ImportError:
            log.exception(_('Could not load listener %s'), listener)

    def trigger(*args, **kwargs):
        for listener in _listeners:
            try:
                listener.call(*args, **kwargs)
            except Exception:
                log.exception(_('Exception in listener %s'), listener)
                if allow_raise:
                    raise
    return trigger


def notify_hooks(module, name, *args, **kwargs):
    """
    Decorator to trigger hooks with the result of a function.

    Additonal args and kwargs can be added to append/update the values the
    hooks are called with. This is the same as:

    hooks = hook_trigger(module, name)

    res = func(*fargs, **fkwargs)
    hkwargs = fkwargs.copy()
    hkwargs.update(kwargs)

    hooks(res, *(args + fargs), **hkwargs)

    """
    def wrap(func):
        @functools.wraps(func)
        def wrapper(*fargs, **fkwargs):
            res = func(*fargs, **fkwargs)
            hkwargs = fkwargs.copy()
            hkwargs.update(kwargs)
            hooks_trigger(module, name)(res, *(args + fargs), **hkwargs)
            return res
        return wrapper
    return wrap
