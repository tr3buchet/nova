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
import logging
import functools

import extensions


log = logging.getLogger(__name__)


def plugins(module, name):
    """
    Helper function to get plugins including entry_points
    """
    return extensions.get(group=module, name=name,
                          consume_entry_points=True)


def plugin_trigger(module=None, name=None):
    """
    Return a callable that will call all hooks registered in the given
    module with the given name with any arguments passed.

    Plugin callables are loaded once and called everytime the callable is
    invoked.

    Exceptions in plugins are caught and logged.

    """
    _plugins = [{'plugin': p,
                 'callable': p.load()} for p in plugins(module, name)]

    def trigger(*args, **kwargs):
        for plugin in _plugins:
            try:
                plugin['callable'](*args, **kwargs)
            except Exception:
                log.exception(_('Exception in plugin %s'), plugin['plugin'])
    return trigger


def notify_plugins(module, name, *args, **kwargs):
    """
    Decorator to trigger plugins with the result of a function.

    Additonal args and kwargs can be added to append/update the values the
    plugins are called with. This is the same as:

    plugins = plugin_trigger(module, name)

    res = func(*fargs, **fkwargs)
    pkwargs = fkwargs.copy()
    pkwargs.update(kwargs)

    plugins(res, *(args + fargs), **pkwargs)

    """
    def wrap(func):
        @functools.wraps(func)
        def wrapper(*fargs, **fkwargs):
            res = func(*fargs, **fkwargs)
            pkwargs = fkwargs.copy()
            pkwargs.update(kwargs)
            plugin_trigger(module, name)(res, *(args + fargs), **pkwargs)
            return res

        return wrapper
    return wrap
